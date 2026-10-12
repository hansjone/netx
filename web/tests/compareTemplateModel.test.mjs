import assert from "node:assert/strict";
import { test } from "node:test";
import { parseTemplateImport, parseIfaceNormText, toOrGroups, fromOrGroups, validateTemplateDraft } from "../src/features/cutover/lib/compareTemplateModel.ts";

const sheet = () => ({ metric_id: "custom", sheet_id: "s", key_fields: ["id"], iface_fields: ["port"], compare_fields: ["count"], field_rules: [], row_filters: [] });
const template = (metrics = [sheet()]) => ({ name: "验收", metrics });

test("invalid second sheet rejects the complete import", () => {
  assert.throws(() => parseTemplateImport(template([sheet(), { ...sheet(), sheet_id: "s2", key_fields: [] }])), /metrics\[1\]/);
});

test("all three port policies and custom fields survive import", () => {
  for (const policy of [true, false, null]) {
    const parsed = parseTemplateImport(template([{ ...sheet(), ignore_port_changes: policy }]));
    assert.equal(parsed.metrics[0].ignore_port_changes, policy);
    assert.deepEqual(parsed.metrics[0].compare_fields, ["count"]);
  }
});

test("legacy single metric import preserves explicit false and ignored fields without mutation", () => {
  const raw = { name: "legacy", ...sheet(), ignore_port_changes: false, ignore_fields: ["count"], field_rules: [{ field: "count", compare: "numeric" }] };
  const original = structuredClone(raw);
  const parsed = parseTemplateImport(raw);
  assert.equal(parsed.metrics[0].ignore_port_changes, false);
  assert.deepEqual(parsed.metrics[0].compare_fields, []);
  assert.equal(parsed.metrics[0].field_rules[0].ignore, true);
  assert.deepEqual(raw, original);
});

test("complex conditions remain intact and cannot become partial editable groups", () => {
  const filters = [{ any: [{ all: [{ any: [{ field: "a", op: "eq", value: 0 }, { field: "b", op: "eq", value: false }] }, { field: "c", op: "empty" }] }] }];
  assert.equal(toOrGroups(filters), null);
  assert.deepEqual(parseTemplateImport(template([{ ...sheet(), row_filters: filters }])).metrics[0].row_filters, filters);
});

test("supported OR/AND editor roundtrip preserves false, zero and list values", () => {
  const groups = [[{ field: "a", op: "eq", value: 0 }, { field: "b", op: "eq", value: false }], [{ field: "c", op: "in", value: [0, false, "up"] }]];
  assert.deepEqual(toOrGroups(fromOrGroups(groups)), groups);
});

test("import rejects unsupported format/version and malformed aliases", () => {
  assert.throws(() => parseTemplateImport({ ...template(), format: "other" }), /unsupported/);
  assert.throws(() => parseTemplateImport({ ...template(), version: 2 }), /unsupported/);
  assert.throws(() => parseTemplateImport({ ...template(), iface_normalize_rules: [{ from: "GE", to: "a" }, { from: "ge", to: "b" }] }), /duplicate_alias/);
  assert.throws(() => parseTemplateImport({ ...template(), iface_normalize_rules: [{ from: "GE" }] }), /alias_pair/);
});

test("alias editor rejects bad lines instead of silently discarding them", () => {
  assert.throws(() => parseIfaceNormText("GE, xe\nbroken"), /:2:/);
  assert.throws(() => parseIfaceNormText("GE, xe\nge\tother"), /duplicate_alias/);
  assert.deepEqual(parseIfaceNormText("# aliases\nGE, xe\nEth\tport"), [{ from: "GE", to: "xe" }, { from: "Eth", to: "port" }]);
});

test("invalid tolerances, modes, duplicate sheets and scalar set membership block saving", () => {
  for (const tolerance of [-1, Infinity, NaN]) assert.throws(() => validateTemplateDraft([{ ...sheet(), field_rules: [{ field: "count", compare: "numeric", tolerance }] }]), /tolerance/);
  assert.throws(() => validateTemplateDraft([{ ...sheet(), field_rules: [{ field: "count", compare: "typo" }] }]), /unknown_compare/);
  assert.throws(() => validateTemplateDraft([sheet(), sheet()]), /duplicate_sheet/);
  assert.throws(() => validateTemplateDraft([{ ...sheet(), row_filters: [{ field: "id", op: "in", value: "a,b" }] }]), /value_list/);
});

import { sheetPayload, formatIfaceNormText, parseValueMapText, formatValueMapText } from "../src/features/cutover/lib/compareTemplateModel.ts";

test("sheet options survive import and the save payload", () => {
  const parsed = parseTemplateImport(template([{ ...sheet(), unmapped_port_policy: "ignore_port", duplicate_pairing: "ordered", builtin_vendor_canonical: false }]));
  const s = parsed.metrics[0];
  assert.equal(s.unmapped_port_policy, "ignore_port");
  assert.equal(s.duplicate_pairing, "ordered");
  assert.equal(s.builtin_vendor_canonical, false);
  const body = sheetPayload(s);
  assert.equal(body.unmapped_port_policy, "ignore_port");
  assert.equal(body.builtin_vendor_canonical, false);
  assert.equal("duplicate_pairing" in sheetPayload({ ...s, duplicate_pairing: "" }), false);
});

test("alias rules keep side and vendor and may repeat per scope", () => {
  const rules = parseIfaceNormText("GE,gei\nGE,GigabitEthernet,before\nEth-Trunk,ae,after,huawei");
  assert.deepEqual(rules, [{ from: "GE", to: "gei" }, { from: "GE", to: "GigabitEthernet", side: "before" }, { from: "Eth-Trunk", to: "ae", side: "after", vendor: "huawei" }]);
  assert.deepEqual(parseIfaceNormText(formatIfaceNormText(rules)), rules);
  assert.throws(() => parseIfaceNormText("GE,gei\nge,x"), /duplicate_alias/);
  assert.throws(() => parseIfaceNormText("GE,gei,middle"), /unknown_alias_side/);
});

test("a vendor-scoped alias with no side survives the text round trip", () => {
  const rules = parseIfaceNormText("Eth-Trunk,ae,both,huawei");
  assert.deepEqual(rules, [{ from: "Eth-Trunk", to: "ae", vendor: "huawei" }]);
  assert.equal(formatIfaceNormText(rules), "Eth-Trunk,ae,both,huawei");
});

test("value maps parse, format and are bounded", () => {
  const map = parseValueMapText("estab,established\n# note\n*down,down");
  assert.deepEqual(map, { estab: "established", "*down": "down" });
  assert.deepEqual(parseValueMapText(formatValueMapText(map)), map);
  assert.throws(() => parseValueMapText("lonely"), /pair_required/);
  assert.throws(() => parseValueMapText(Array.from({ length: 501 }, (_, i) => `a${i},b`).join("\n")), /too_large/);
});

test("directional compare modes need a rank and a field can carry one rule per side", () => {
  const base = sheet();
  assert.throws(() => validateTemplateDraft([{ ...base, field_rules: [{ field: "state", compare: "not_worse" }] }]), /rank_list_required/);
  validateTemplateDraft([{ ...base, field_rules: [{ field: "state", compare: "not_worse", rank: ["up", "down"] }, { field: "count", compare: "numeric_min", tolerance: 5 }] }]);
  validateTemplateDraft([{ ...base, field_rules: [{ field: "state", side: "before", value_map: { a: "b" } }, { field: "state", side: "after", value_map: { c: "d" } }] }]);
  assert.throws(() => validateTemplateDraft([{ ...base, field_rules: [{ field: "state", value_map: { a: "b" } }, { field: "state", value_map: { c: "d" } }] }]), /duplicate_field/);
  assert.throws(() => validateTemplateDraft([{ ...base, field_rules: [{ field: "state", value_map: { a: 1 } }] }]), /string_pairs/);
});

test("filter side survives the editor round trip", () => {
  const groups = toOrGroups([{ field: "type", op: "eq", value: "dynamic", side: "before" }]);
  assert.deepEqual(fromOrGroups(groups), [{ field: "type", op: "eq", value: "dynamic", side: "before" }]);
});
