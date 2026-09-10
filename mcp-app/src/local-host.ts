// Local verification host using the official bridge, not a custom wire protocol.
import { AppBridge, PostMessageTransport, getToolUiResourceUri } from "@modelcontextprotocol/ext-apps/app-bridge";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";
import { CallToolResultSchema } from "@modelcontextprotocol/sdk/types.js";

const iframe = document.getElementById("app");
const theme = document.getElementById("theme");
const status = document.getElementById("host-status");
const reload = document.getElementById("reload");
if (!(iframe instanceof HTMLIFrameElement && theme instanceof HTMLSelectElement && status instanceof HTMLElement && reload instanceof HTMLButtonElement)) throw new Error("Invalid host markup");

const client = new Client({ name: "Pixelpitch local preview", version: "0.1.0" }, { capabilities: { experimental: { "io.modelcontextprotocol/ui": { mimeTypes: ["text/html;profile=mcp-app"] } } } });
let bridge: AppBridge | undefined;
try {
  await client.connect(new StreamableHTTPClientTransport(new URL("/mcp", location.href)));
  const { tools } = await client.listTools();
  const tool = tools.find((candidate) => candidate.name === "open_pixelpitch");
  const uri = tool && getToolUiResourceUri(tool);
  if (!tool || !uri) throw new Error("Pixelpitch did not advertise an MCP App.");
  const saved = sessionStorage.getItem("pixelpitch-local-workspace");
  const result = saved ? CallToolResultSchema.parse(JSON.parse(saved)) : CallToolResultSchema.parse(await client.callTool({ name: tool.name, arguments: { topic: "" } }));
  sessionStorage.setItem("pixelpitch-local-workspace", JSON.stringify(result));
  const resource = await client.readResource({ uri });
  const html = resource.contents.find((content) => "text" in content)?.text;
  if (typeof html !== "string") throw new Error("Build the MCP App before opening the preview.");
  const htmlText = html;

  async function mount(): Promise<void> {
    if (!(iframe instanceof HTMLIFrameElement && theme instanceof HTMLSelectElement && status instanceof HTMLElement)) return;
    if (bridge) {
      await bridge.teardownResource({}).catch(() => {});
      await bridge.close();
    }
    const appWindow = iframe.contentWindow;
    if (!appWindow) throw new Error("Preview frame is unavailable.");
    const next = new AppBridge(client, { name: "Pixelpitch local preview", version: "0.1.0" }, { openLinks: {}, serverTools: {}, logging: {} }, { hostContext: { theme: theme.value === "dark" ? "dark" : "light", displayMode: "inline" } });
    next.oninitialized = async () => {
      await next.sendToolInput({ arguments: { topic: "" } });
      await next.sendToolResult(result);
      status.hidden = true;
      performance.mark("pixelpitch-app-connected");
    };
    next.onsizechange = ({ height }) => { if (typeof height === "number") iframe.style.height = `${Math.min(Math.max(height, 400), 12000)}px`; };
    next.onopenlink = async ({ url }) => {
      if (new URL(url).protocol !== "https:") throw new Error("Only HTTPS downloads are allowed.");
      window.open(url, "_blank", "noopener,noreferrer");
      return {};
    };
    await next.connect(new PostMessageTransport(appWindow, appWindow));
    bridge = next;
    // No external resources or blob URLs. The nested srcdoc is script-free.
    const csp = "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; font-src data:; frame-src 'self'; connect-src 'none'; base-uri 'none'\">";
    iframe.srcdoc = htmlText.replace(/<head>/i, `<head>${csp}`);
  }
  reload.addEventListener("click", () => void mount().catch((error: unknown) => { status.hidden = false; status.textContent = error instanceof Error ? error.message : "Could not reconnect."; }));
  theme.addEventListener("change", () => { const value = theme.value === "dark" ? "dark" : "light"; document.body.dataset.theme = value; bridge?.setHostContext({ theme: value }); });
  await mount();
} catch (error) {
  status.textContent = error instanceof Error ? error.message : "The local MCP connection failed.";
}
