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
"""Unit tests for BOLT perf-regression variant tagging (bolt_variant.py).

These guard the backward-compatibility contract that keeps BOLT scaffolding
inert for normal builds:
- variant UNSET -> no-op: no field added, match_keys returned unchanged.
- variant SET   -> record tagged + match_keys augmented (once), whitespace
  stripped, so bolted runs compare only against bolted history.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
BOLT_VARIANT_PY = REPO_ROOT / "tests" / "integration" / "defs" / "perf" / "bolt_variant.py"


@pytest.fixture(scope="module")
def bv():
    spec = importlib.util.spec_from_file_location("bolt_variant", BOLT_VARIANT_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_unset_is_noop(bv, monkeypatch):
    monkeypatch.delenv(bv.BOLT_VARIANT_ENV, raising=False)
    extra_fields = {"s_stage_name": "x"}
    match_keys = ["s_gpu_type", "s_runtime"]
    out = bv.apply_bolt_variant(extra_fields, match_keys)
    # No tagging and no baseline-key change for a normal (un-bolted) build.
    assert bv.BOLT_VARIANT_FIELD not in extra_fields
    assert out == ["s_gpu_type", "s_runtime"]


def test_empty_env_is_noop(bv, monkeypatch):
    monkeypatch.setenv(bv.BOLT_VARIANT_ENV, "   ")
    extra_fields = {}
    match_keys = ["s_gpu_type"]
    out = bv.apply_bolt_variant(extra_fields, match_keys)
    assert bv.BOLT_VARIANT_FIELD not in extra_fields
    assert out == ["s_gpu_type"]


def test_set_tags_record_and_augments_match_keys(bv, monkeypatch):
    monkeypatch.setenv(bv.BOLT_VARIANT_ENV, "bolt")
    extra_fields = {"s_stage_name": "x"}
    match_keys = ["s_gpu_type", "s_runtime"]
    out = bv.apply_bolt_variant(extra_fields, match_keys)
    assert extra_fields[bv.BOLT_VARIANT_FIELD] == "bolt"
    assert out == ["s_gpu_type", "s_runtime", bv.BOLT_VARIANT_FIELD]


def test_set_strips_whitespace(bv, monkeypatch):
    monkeypatch.setenv(bv.BOLT_VARIANT_ENV, "  bolt\n")
    assert bv.get_bolt_variant() == "bolt"


def test_augment_is_idempotent(bv, monkeypatch):
    # If the variant field is already a match key, don't duplicate it.
    monkeypatch.setenv(bv.BOLT_VARIANT_ENV, "bolt")
    extra_fields = {}
    match_keys = ["s_gpu_type", bv.BOLT_VARIANT_FIELD]
    out = bv.apply_bolt_variant(extra_fields, match_keys)
    assert out.count(bv.BOLT_VARIANT_FIELD) == 1
    assert out == ["s_gpu_type", bv.BOLT_VARIANT_FIELD]


def test_does_not_mutate_input_match_keys(bv, monkeypatch):
    # The caller's list must not be mutated; a new list is returned.
    monkeypatch.setenv(bv.BOLT_VARIANT_ENV, "bolt")
    match_keys = ["s_gpu_type"]
    out = bv.apply_bolt_variant({}, match_keys)
    assert match_keys == ["s_gpu_type"]
    assert out == ["s_gpu_type", bv.BOLT_VARIANT_FIELD]
