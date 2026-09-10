import { App, applyDocumentTheme, applyHostStyleVariables } from "@modelcontextprotocol/ext-apps";
import DOMPurify from "dompurify";
import { z } from "zod";
import "./style.css";

const connectionSchema = z.object({ workspace: z.string(), capability: z.string() });
const briefSchema = z.object({ topic: z.string(), audience: z.string(), goal: z.string(), slide_count: z.number(), brand_id: z.string(), style_id: z.string(), template_revision: z.string() });
const jobSchema = z.object({
  id: z.string(), status: z.enum(["queued", "running", "cancelling", "completed", "failed", "cancelled", "interrupted"]),
  created: z.number(), updated: z.number(), elapsed: z.number(), brief: briefSchema,
  progress: z.object({ stage: z.string().optional(), title: z.string().optional() }),
  slides: z.array(z.object({ slide_index: z.number(), title: z.string(), revision: z.number(), kind: z.enum(["draft", "export"]).default("draft") })),
  result: z.object({ https_url: z.string().nullish(), slide_count: z.number().optional() }).nullable(),
  error: z.string().nullable(),
});
type Job = z.infer<typeof jobSchema>;
type Brief = z.infer<typeof briefSchema>;

function element(id: string): HTMLElement {
  const node = document.getElementById(id);
  if (!node) throw new Error(`Missing interface element: ${id}`);
  return node;
}
function input(id: string): HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement {
  const node = element(id);
  if (!(node instanceof HTMLInputElement || node instanceof HTMLTextAreaElement || node instanceof HTMLSelectElement)) throw new Error(`Invalid field: ${id}`);
  return node;
}
function button(id: string): HTMLButtonElement {
  const node = element(id);
  if (!(node instanceof HTMLButtonElement)) throw new Error(`Invalid button: ${id}`);
  return node;
}
function formElement(): HTMLFormElement { const node = element("brief"); if (!(node instanceof HTMLFormElement)) throw new Error("Invalid brief"); return node; }
function previewElement(): HTMLIFrameElement { const node = element("preview"); if (!(node instanceof HTMLIFrameElement)) throw new Error("Invalid preview"); return node; }
function fieldsetElement(): HTMLFieldSetElement { const node = element("fields"); if (!(node instanceof HTMLFieldSetElement)) throw new Error("Invalid fields"); return node; }
const form = formElement();
const frame = previewElement();
const fields = fieldsetElement();
const app = new App({ name: "Pixelpitch", version: "0.1.0" });
let connection: z.infer<typeof connectionSchema> | undefined;
let job: Job | null = null;
let draftBrief: Brief | undefined;
let submitting = false;
let disposed = false;
let polling = false;
let pollFailures = 0;
let timer: ReturnType<typeof setTimeout> | undefined;
let activeSlide = -1;
let renderedRevision = -1;
let previewRequest = 0;
let listSignature = "";
let request = { id: crypto.randomUUID(), brief: "" };
const readyChoices = new Set<string>();

function busy(): boolean { return job !== null && ["queued", "running", "cancelling"].includes(job.status); }
function text(id: string, value: string): void { element(id).textContent = value; }
function showError(message: string): void { text("error", message); element("error").hidden = false; }
function clearError(): void { element("error").hidden = true; }

async function call(name: string, args: Record<string, unknown> = {}, timeout = 15000): Promise<unknown> {
  if (!connection) throw new Error("Pixelpitch has not connected yet.");
  const result = await app.callServerTool({ name, arguments: { ...args, ...connection } }, { timeout });
  if (result.isError) {
    const message = result.content.find((part) => part.type === "text");
    throw new Error(message?.type === "text" ? message.text : "The request could not be completed.");
  }
  return result.structuredContent;
}

function enable(): void {
  const templateSelected = Boolean(input("template").value);
  input("brand").disabled = templateSelected || !readyChoices.has("brands");
  input("style").disabled = templateSelected || !readyChoices.has("styles");
  fields.disabled = busy() || submitting;
  button("generate").disabled = !connection || submitting || busy() || !input("topic").value.trim() || !form.checkValidity() || !(input("brand").value || templateSelected);
}

function readBrief(): Brief {
  return briefSchema.parse({ topic: input("topic").value.trim(), audience: input("audience").value.trim(), goal: input("goal").value.trim(), slide_count: Number(input("slide-count").value), brand_id: input("brand").value, style_id: input("style").value, template_revision: input("template").value });
}

function restore(brief: Brief): void {
  draftBrief = brief;
  for (const [id, value] of Object.entries({ topic: brief.topic, audience: brief.audience, goal: brief.goal, "slide-count": String(brief.slide_count), brand: brief.brand_id, style: brief.style_id, template: brief.template_revision })) input(id).value = value;
}

async function choices(kind: "brands" | "styles" | "templates"): Promise<void> {
  const id = { brands: "brand", styles: "style", templates: "template" }[kind];
  const select = input(id);
  if (!(select instanceof HTMLSelectElement)) return;
  try {
    const result = z.object({ available: z.boolean(), choices: z.array(z.object({ id: z.string().optional(), revision: z.string().optional(), name: z.string() })) }).parse(await call("deck_choices", { kind }, 70000));
    const selected = select.value || (draftBrief ? { brands: draftBrief.brand_id, styles: draftBrief.style_id, templates: draftBrief.template_revision }[kind] : "");
    select.replaceChildren(new Option(kind === "brands" ? "Choose a brand" : kind === "styles" ? "Let the deck guide the style" : "No reference template", ""));
    for (const choice of result.choices) select.add(new Option(choice.name, choice.id || choice.revision || ""));
    select.value = selected;
    if (result.available) {
      readyChoices.add(kind);
      select.disabled = false;
    } else {
      text("choices-note", "Some design choices are unavailable. You can use the options that loaded or reload them.");
      button("reload-choices").hidden = false;
    }
  } catch {
    text("choices-note", "Some design choices could not load. Your brief is still here.");
    button("reload-choices").hidden = false;
  }
  enable();
}

function schedule(): void {
  clearTimeout(timer);
  if (!disposed && busy()) timer = setTimeout(() => void poll(), Math.min(15000, Math.max(document.hidden ? 5000 : 1500, pollFailures * 3000)));
}

async function poll(): Promise<void> {
  if (polling || disposed) return;
  polling = true;
  try {
    const result = z.object({ job: jobSchema.nullable() }).parse(await call("deck_status", job ? { job_id: job.id } : {}));
    if (disposed) return;
    pollFailures = 0;
    element("reconnect").hidden = true;
    element("connection").hidden = true;
    clearError();
    if (result.job) {
      if (!job) restore(result.job.brief);
      update(result.job);
    }
  } catch {
    pollFailures += 1;
    showError("Connection interrupted. The job may still be running. Reconnect before starting another deck.");
    element("reconnect").hidden = false;
  } finally {
    polling = false;
    schedule();
  }
}

function update(next: Job): void {
  if (job?.id === next.id && next.updated < job.updated) return;
  if (job?.id !== next.id) { activeSlide = -1; renderedRevision = -1; listSignature = ""; }
  job = next;
  element("work").hidden = false;
  form.hidden = true;
  text("heading", next.brief.topic);
  text("intro", `${next.brief.audience || "Presentation"} · ${next.brief.slide_count} slides requested`);
  const titles = { queued: "Your deck is queued", running: next.progress.title || "Preparing your deck", cancelling: "Stopping generation", cancelled: "Generation cancelled", completed: next.result?.https_url ? "Your deck is ready" : "Download unavailable", failed: "Generation stopped", interrupted: "Worker interrupted" };
  text("work-title", titles[next.status]);
  const details = {
    queued: "Your request is saved. The worker will pick it up when available.",
    running: next.slides.length ? "Browse the available drafts while work continues." : "The first draft will appear when it has been written.",
    cancelling: "Cancellation requested. Waiting for the worker to stop.",
    cancelled: "Your brief and available drafts are saved. Edit the brief to try again.",
    completed: next.result?.https_url ? "The PowerPoint is available to download. Each preview is labelled as an export or an authoring draft." : "Generation finished, but no download link was returned.",
    failed: next.error || "Review your brief and try again.",
    interrupted: next.error || "The worker stopped responding. Your brief and drafts are saved.",
  };
  // Avoid re-announcing unchanged status on every poll.
  if (element("phase").textContent !== details[next.status]) text("phase", details[next.status]);
  text("elapsed", `${Math.floor(next.elapsed / 60)}:${String(Math.floor(next.elapsed % 60)).padStart(2, "0")} elapsed`);
  const since = Math.max(0, Math.floor(Date.now() / 1000 - next.updated));
  text("freshness", busy() && since >= 15 ? `Last confirmed milestone ${since}s ago. Waiting for the next update.` : "");
  button("cancel").hidden = !busy();
  button("cancel").disabled = next.status === "cancelling";
  button("edit").hidden = busy();
  button("download").hidden = next.status !== "completed" || !next.result?.https_url;
  element("waiting").hidden = next.slides.length > 0 || !busy();
  text("lane-note", next.brief.template_revision ? "Reference-template drafts become available after the template patches are assembled." : "Your first slide is not ready yet.");
  element("drafts").hidden = next.slides.length === 0;
  const exported = next.slides.filter((slide) => slide.kind === "export").length;
  text("draft-count", exported ? `${exported} exported · ${next.slides.length - exported} drafts` : `${next.slides.length} ${next.slides.length === 1 ? "draft" : "drafts"} available`);
  const signature = JSON.stringify(next.slides);
  if (signature !== listSignature) {
    listSignature = signature;
    const items = next.slides.map((slide) => {
      const item = document.createElement("li");
      const control = document.createElement("button");
      const number = document.createElement("span");
      number.className = "slide-number";
      number.textContent = String(slide.slide_index + 1).padStart(2, "0");
      control.append(number, document.createTextNode(slide.title));
      control.dataset.index = String(slide.slide_index);
      control.addEventListener("click", () => void selectSlide(slide.slide_index));
      item.append(control);
      return item;
    });
    element("slides").replaceChildren(...items);
    const selected = next.slides.find((slide) => slide.slide_index === activeSlide) || next.slides[0];
    if (selected && (selected.slide_index !== activeSlide || selected.revision !== renderedRevision)) void selectSlide(selected.slide_index);
    markSelection();
  }
  enable();
}

function markSelection(): void {
  for (const node of element("slides").querySelectorAll("button")) node.setAttribute("aria-current", String(Number(node.dataset.index) === activeSlide));
}

async function selectSlide(index: number): Promise<void> {
  if (!job) return;
  activeSlide = index;
  const serial = ++previewRequest;
  markSelection();
  text("preview-title", job.slides.find((slide) => slide.slide_index === index)?.title || "Draft preview");
  text("preview-message", "Loading this slide…");
  element("preview-message").hidden = false;
  frame.hidden = true;
  try {
    const draft = z.object({ html: z.string().max(2_000_000), title: z.string(), revision: z.number(), kind: z.enum(["draft", "export"]).default("draft") }).parse(await call("deck_slide", { job_id: job.id, index }));
    if (disposed || serial !== previewRequest) return;
    const clean = DOMPurify.sanitize(draft.html, { WHOLE_DOCUMENT: true, ADD_TAGS: ["style"], FORBID_TAGS: ["script", "iframe", "object", "embed", "form", "input", "button", "base", "meta", "link", "audio", "video"], FORBID_ATTR: ["href", "srcset", "target", "action", "formaction"] });
    const csp = "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:; base-uri 'none'; form-action 'none'\">";
    const html = clean.replace(/<head>/i, `<head><meta charset="utf-8">${csp}`);
    frame.srcdoc = html;
    frame.title = draft.kind === "export" ? "Exported PowerPoint slide" : "Authored slide draft";
    text("preview-kind", draft.kind === "export" ? "Actual PowerPoint export. The downloaded deck remains editable." : "Authoring draft. Layout and assets may change during export checks.");
    frame.hidden = false;
    element("preview-message").hidden = true;
    renderedRevision = draft.revision;
    fitPreview();
  } catch {
    if (serial !== previewRequest) return;
    text("preview-message", "This slide could not load. Select the slide to try again.");
  }
}

function fitPreview(): void { frame.style.transform = `scale(${element("preview-stage").clientWidth / 1280})`; }
const observer = new ResizeObserver(fitPreview);
observer.observe(element("preview-stage"));

form.addEventListener("input", enable);
form.addEventListener("change", enable);
async function submit(event: Event): Promise<void> {
  event.preventDefault();
  if (submitting || busy() || !form.reportValidity()) return;
  const brief = readBrief();
  const encoded = JSON.stringify(brief);
  if (request.brief !== encoded) request = { id: crypto.randomUUID(), brief: encoded };
  submitting = true;
  clearError();
  enable();
  button("generate").textContent = "Sending request…";
  try {
    const result = z.object({ job: jobSchema }).parse(await call("start_deck", { request_id: request.id, brief }));
    update(result.job);
    element("work-title").focus();
    schedule();
  } catch (error) {
    showError(error instanceof Error ? error.message : "Could not submit your brief.");
    element("reconnect").hidden = false;
  } finally {
    submitting = false;
    button("generate").textContent = "Generate deck";
    enable();
  }
}
// Some MCP hosts omit allow-forms. Handle activation before the browser's
// native submission step, which those sandboxes block before a submit event.
form.addEventListener("submit", submit);
button("generate").addEventListener("click", submit);

button("reconnect").addEventListener("click", () => void poll());
button("reload-choices").addEventListener("click", () => { for (const kind of ["brands", "styles", "templates"] as const) void choices(kind); });
button("cancel").addEventListener("click", async () => {
  if (!job) return;
  button("cancel").disabled = true;
  try { update(z.object({ job: jobSchema }).parse(await call("cancel_deck", { job_id: job.id })).job); }
  catch { showError("Cancellation was not confirmed. Reconnect to check the job before trying again."); button("cancel").disabled = false; }
});
button("edit").addEventListener("click", () => {
  if (!job || busy()) return;
  restore(job.brief);
  job = null;
  request = { id: crypto.randomUUID(), brief: "" };
  clearTimeout(timer);
  element("work").hidden = true;
  form.hidden = false;
  text("heading", "Refine your presentation");
  text("intro", "Your previous brief is saved below. Review it before starting another build.");
  enable();
  input("topic").focus();
});
button("download").addEventListener("click", async () => {
  const url = job?.result?.https_url;
  if (!url || !URL.canParse(url) || new URL(url).protocol !== "https:") { showError("No valid download link was returned."); return; }
  try { await app.openLink({ url }); } catch { showError("The host could not open the download. Try again from Gemini Enterprise."); }
});

app.ontoolresult = (result) => {
  if (connection) return;
  const parsed = z.object({ pixelpitch: connectionSchema }).safeParse(result._meta);
  if (!parsed.success) return;
  connection = parsed.data.pixelpitch;
  const initial = z.object({ topic: z.string(), max_slides: z.number().optional() }).safeParse(result.structuredContent);
  if (initial.success) {
    // Preserve anything typed while the initial call was in flight.
    if (!input("topic").value) input("topic").value = initial.data.topic;
    input("slide-count").setAttribute("max", String(initial.data.max_slides || 20));
  }
  text("connection", "Loading design choices. You can start writing your brief.");
  for (const kind of ["brands", "styles", "templates"] as const) void choices(kind);
  void poll();
};
app.onhostcontextchanged = (context) => {
  if (context.theme) applyDocumentTheme(context.theme);
  if (context.styles?.variables) applyHostStyleVariables(context.styles.variables);
};
app.onteardown = async () => {
  disposed = true;
  clearTimeout(timer);
  observer.disconnect();
  return {};
};
try {
  await app.connect();
  const context = app.getHostContext();
  if (context?.theme) applyDocumentTheme(context.theme);
  if (context?.styles?.variables) applyHostStyleVariables(context.styles.variables);
} catch {
  showError("Pixelpitch could not connect to its host. Reopen the app from chat.");
}
