# PPTX quality and delivery gates

Do not call the deck complete until all relevant gates pass.

## Structure

- requested slide count equals visible slide count;
- native customer-template routes retain expected picture/group relationships;
- no unresolved placeholder prompts or prototype copy;
- output opens as valid OOXML and contains the expected media/theme parts.

## Visuals

- render the generated PPTX itself and inspect preview pages;
- image-bearing briefs have image-bearing slides, not only orphaned media parts;
- avoid accidental repetition when several template specimens are available;
- preserve intentional blank/content pages when they serve the narrative.

## Readability

- ordinary presentation copy is at least 18pt unless the template's approved
  legal/footer role requires smaller text;
- body density fits the chosen source slide; move detail to notes or a content
  layout rather than shrinking it into unreadability;
- no crop, overflow, collision, or off-slide text.

## Delivery

- preview images come from the converted PPTX;
- the download action uses the renderer-returned GCS URL and correct PPTX MIME;
- test the URL with a byte-range request and verify the ZIP `PK` signature;
- never send binary PPTX through a text-preview API or claim success from a
  local filename alone.
