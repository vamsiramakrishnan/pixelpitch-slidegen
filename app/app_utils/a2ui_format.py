# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Single source of truth for the A2UI inference format and component catalog.

Gemini Enterprise negotiates A2UI on ``catalogId``. It only renders surfaces
built against its own composite catalog:

    https://www.gstatic.com/vertexaisearch/a2ui/v0_9/gemini_enterprise_composite_catalog.json

That catalog is the union of the standard Material catalog, the A2UI basic
catalog, and Gemini Enterprise's custom components, so the basic components
this agent uses remain valid. Advertising the plain basic catalog instead
causes Gemini Enterprise to silently drop the surface and render only the
accompanying text.

The catalog is vendored under ``app/assets/`` so the container has no network
dependency at startup. Both the system-prompt generator (``app.agent``) and the
A2A event converter (``app.app_utils.a2a``) MUST use this one instance, so the
catalog advertised on the agent card always matches the catalog referenced by
emitted ``createSurface`` messages.
"""

from __future__ import annotations

import functools
import os

from a2ui.inference_formats.direct_json import DirectJsonFormat
from a2ui.schema.catalog import CatalogConfig
from a2ui.schema.common_modifiers import remove_strict_validation
from a2ui.schema.constants import VERSION_0_9

_ASSETS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets"
)

# Vendored copy of the Gemini Enterprise composite catalog.
GEMINI_ENTERPRISE_CATALOG_PATH = os.path.join(
    _ASSETS_DIR, "gemini_enterprise_composite_catalog.json"
)

_CATALOG_NAME = "composite"


@functools.cache
def get_a2ui_format() -> DirectJsonFormat:
    """Returns the process-wide A2UI format bound to the GE composite catalog."""
    return DirectJsonFormat(
        version=VERSION_0_9,
        catalogs=[
            CatalogConfig.from_path(
                name=_CATALOG_NAME,
                catalog_path=GEMINI_ENTERPRISE_CATALOG_PATH,
            )
        ],
        schema_modifiers=[remove_strict_validation],
    )


def get_supported_catalog_ids() -> list[str]:
    """Catalog ids to advertise in the A2UI agent-card extension."""
    return list(get_a2ui_format().supported_catalog_ids)
