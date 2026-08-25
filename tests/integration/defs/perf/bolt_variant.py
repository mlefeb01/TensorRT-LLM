# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""BOLT build-variant tagging for perf-sanity regression records.

When a build under test has LLVM BOLT profiles applied, CI sets the
``TRTLLM_BOLT_VARIANT`` environment variable (e.g. ``bolt``). Perf records for
that run are then tagged with the variant and compared only against
same-variant history, so bolted and un-bolted baselines never contaminate each
other (they have different binary layouts and thus different perf).

BOLT is a host-side binary layout optimization, so its perf delta is
independent of the test itself -- reusing the existing perf-sanity metrics is
sufficient to catch regressions; only the baseline *series* needs to be kept
separate. That separation is achieved purely by adding one field to the
uploaded record and to the baseline ``match_keys``.

This module intentionally has no imports beyond the standard library so it can
be exercised by lightweight unit tests without pulling in the perf/CI stack.

Backward compatibility: when ``TRTLLM_BOLT_VARIANT`` is unset (the default for
every normal build), these helpers are a no-op -- records and baseline matching
are byte-for-byte unchanged, so existing (un-bolted) baselines keep working.
"""

from __future__ import annotations

import os
from typing import Dict, List

#: OpenSearch field carrying the BOLT build variant (e.g. "bolt").
BOLT_VARIANT_FIELD = "s_bolt_variant"

#: Environment variable CI sets to mark a bolted build under test.
BOLT_VARIANT_ENV = "TRTLLM_BOLT_VARIANT"


def get_bolt_variant() -> str:
    """Return the normalized BOLT build variant for this run, or "" if none."""
    return os.environ.get(BOLT_VARIANT_ENV, "").strip()


def apply_bolt_variant(extra_fields: Dict[str, str], match_keys: List[str]) -> List[str]:
    """Tag perf records + baseline matching with the BOLT variant, if set.

    Mutates *extra_fields* in place (adds the variant field to every uploaded
    record) and returns a *match_keys* list augmented with the variant field so
    a bolted run only matches bolted history.

    No-op returning *match_keys* unchanged when ``TRTLLM_BOLT_VARIANT`` is unset,
    so non-BOLT builds are entirely unaffected.
    """
    variant = get_bolt_variant()
    if not variant:
        return match_keys
    extra_fields[BOLT_VARIANT_FIELD] = variant
    if BOLT_VARIANT_FIELD in match_keys:
        return match_keys
    return list(match_keys) + [BOLT_VARIANT_FIELD]
