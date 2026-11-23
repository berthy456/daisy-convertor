// The cover helpers that work without a canvas.

import assert from "node:assert/strict";
import { test } from "node:test";

const { COVER_SIZE, IMAGE_SIZE, isReadableUrl, screen, searchUrls } = await import("../../web/cover.js");

test("the two search links are image searches on the same words", () => {
  const google = new URL(searchUrls("Helen Grant Ruby").google);
  assert.equal(google.host, "www.google.com");
  assert.equal(google.searchParams.get("tbm"), "isch");
  assert.equal(google.searchParams.get("q"), "Helen Grant Ruby");

  const ddg = new URL(searchUrls("Helen Grant Ruby").ddg);
  assert.equal(ddg.host, "duckduckgo.com");
  assert.equal(ddg.searchParams.get("ia"), "images");
  assert.equal(ddg.searchParams.get("q"), "Helen Grant Ruby");
});

test("only http(s) links are fetched", () => {
  const base = "https://berthy456.github.io/daisy2telmi/";
  assert.ok(isReadableUrl("https://example.com/cover.jpg", base));
  assert.ok(isReadableUrl("/local/cover.png", base));
  assert.ok(isReadableUrl("cover.png", base));
  assert.ok(!isReadableUrl("file:///etc/passwd", base));
  assert.ok(!isReadableUrl("javascript:alert(1)", base));
  assert.ok(!isReadableUrl("data:image/png;base64,AAA", base));
  assert.ok(!isReadableUrl("", base));
  assert.ok(!isReadableUrl("not a url at all", "not a base either"));
});

test("the image sizes are the ones Telmi uses", () => {
  assert.deepEqual(IMAGE_SIZE, [640, 480]);
  assert.deepEqual(COVER_SIZE, [512, 512]);
});

test("each player draws at its own screen", () => {
  assert.deepEqual(screen("telmi"), [[640, 480], [512, 512]]);
  assert.deepEqual(screen("lunii"), [[320, 240], [320, 240]]);
  assert.deepEqual(screen("lunii-pk"), [[320, 240], [320, 240]]);
  assert.deepEqual(screen("flam"), [[320, 212], [104, 136]]);
});
