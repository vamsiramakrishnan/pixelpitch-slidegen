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

"""Pixelpitch slidegen ADK composition root."""

import os

from google.adk.apps import App

from app.pixelpitch_agent import PixelpitchAgent

# ADK remains the transport/session/A2A host. It is deliberately not an
# LlmAgent: deterministic Python owns intake and A2UI composition, while the
# isolated Antigravity worker remains the only reasoning system used to author
# slides.
root_agent = PixelpitchAgent(
    name="pixelpitch_slidegen",
    queue_decks=bool(os.getenv("SLIDEGEN_A2A_JOB_DB")),
    description=(
        "Generates brand-guideline-driven presentation decks and delivers "
        "editable PPTX files stored in Google Cloud Storage."
    ),
)

app = App(root_agent=root_agent, name="app")
