import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { inflateSync } from "node:zlib";

import { describe, expect, it } from "vitest";

const publicRoot = resolve(import.meta.dirname, "../public");

interface ManifestIcon {
  src: string;
  sizes: string;
  type: string;
  purpose: string;
}

function manifestIcons(value: unknown): readonly ManifestIcon[] {
  if (!value || typeof value !== "object" || !("icons" in value) || !Array.isArray(value.icons)) {
    throw new Error("PWA manifest icons are missing");
  }
  return value.icons.map((icon) => {
    if (
      !icon
      || typeof icon !== "object"
      || !("src" in icon)
      || typeof icon.src !== "string"
      || !("sizes" in icon)
      || typeof icon.sizes !== "string"
      || !("type" in icon)
      || typeof icon.type !== "string"
      || !("purpose" in icon)
      || typeof icon.purpose !== "string"
    ) {
      throw new Error("PWA manifest icon entry is invalid");
    }
    return {
      src: icon.src,
      sizes: icon.sizes,
      type: icon.type,
      purpose: icon.purpose,
    };
  });
}

function pngSize(path: string): { width: number; height: number } {
  const buffer = readFileSync(path);
  expect(buffer.subarray(1, 4).toString("ascii")).toBe("PNG");
  return {
    width: buffer.readUInt32BE(16),
    height: buffer.readUInt32BE(20),
  };
}

interface PngAlphaStats {
  width: number;
  height: number;
  cornerAlpha: readonly [number, number, number, number];
  transparentPixels: number;
  solidPixels: number;
  partialPixels: number;
  nonTransparentPixels: number;
  nonWhiteGlyphPixels: number;
  bounds: { minX: number; minY: number; maxX: number; maxY: number };
  nearWhitePixels: number;
  nearWhiteOuterPixels: number;
  nearWhiteBounds: { minX: number; minY: number; maxX: number; maxY: number };
}

function paethPredictor(left: number, above: number, upperLeft: number): number {
  const estimate = left + above - upperLeft;
  const leftDistance = Math.abs(estimate - left);
  const aboveDistance = Math.abs(estimate - above);
  const upperLeftDistance = Math.abs(estimate - upperLeft);
  if (leftDistance <= aboveDistance && leftDistance <= upperLeftDistance) return left;
  if (aboveDistance <= upperLeftDistance) return above;
  return upperLeft;
}

function byteAt(bytes: Uint8Array, index: number, context: string): number {
  if (!Number.isInteger(index) || index < 0 || index >= bytes.length) {
    throw new Error(`PNG ${context} byte is out of bounds: ${index}`);
  }
  const value = bytes[index];
  if (value === undefined) {
    throw new Error(`PNG ${context} byte is missing: ${index}`);
  }
  return value;
}

function rgbaPngAlphaStats(path: string): PngAlphaStats {
  const png = readFileSync(path);
  expect(png.subarray(0, 8)).toEqual(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]));
  const width = png.readUInt32BE(16);
  const height = png.readUInt32BE(20);
  expect({
    bitDepth: byteAt(png, 24, "bit depth"),
    colorType: byteAt(png, 25, "color type"),
    compression: byteAt(png, 26, "compression method"),
    filter: byteAt(png, 27, "filter method"),
    interlace: byteAt(png, 28, "interlace method"),
  }).toEqual({ bitDepth: 8, colorType: 6, compression: 0, filter: 0, interlace: 0 });

  const idatChunks: Buffer[] = [];
  for (let offset = 8; offset < png.length;) {
    const chunkLength = png.readUInt32BE(offset);
    const chunkType = png.subarray(offset + 4, offset + 8).toString("ascii");
    if (chunkType === "IDAT") idatChunks.push(png.subarray(offset + 8, offset + 8 + chunkLength));
    offset += 12 + chunkLength;
  }

  const bytesPerPixel = 4;
  const rowLength = width * bytesPerPixel;
  const filtered = inflateSync(Buffer.concat(idatChunks));
  expect(filtered.length).toBe((rowLength + 1) * height);
  const pixels = Buffer.alloc(rowLength * height);
  let sourceOffset = 0;
  for (let y = 0; y < height; y += 1) {
    const filterType = byteAt(filtered, sourceOffset, `row ${y} filter`);
    sourceOffset += 1;
    for (let x = 0; x < rowLength; x += 1) {
      const raw = byteAt(filtered, sourceOffset + x, `row ${y} payload`);
      const outputOffset = y * rowLength + x;
      const left = x >= bytesPerPixel
        ? byteAt(pixels, outputOffset - bytesPerPixel, "left predictor")
        : 0;
      const above = y > 0 ? byteAt(pixels, outputOffset - rowLength, "above predictor") : 0;
      const upperLeft = y > 0 && x >= bytesPerPixel
        ? byteAt(pixels, outputOffset - rowLength - bytesPerPixel, "upper-left predictor")
        : 0;
      const predictor = filterType === 0
        ? 0
        : filterType === 1
          ? left
          : filterType === 2
            ? above
            : filterType === 3
              ? Math.floor((left + above) / 2)
              : filterType === 4
                ? paethPredictor(left, above, upperLeft)
                : -1;
      if (predictor < 0) throw new Error(`Unsupported PNG filter: ${filterType}`);
      pixels[outputOffset] = (raw + predictor) & 0xff;
    }
    sourceOffset += rowLength;
  }

  let transparentPixels = 0;
  let solidPixels = 0;
  let partialPixels = 0;
  let nonTransparentPixels = 0;
  let nonWhiteGlyphPixels = 0;
  let nearWhitePixels = 0;
  let nearWhiteOuterPixels = 0;
  let minX = width;
  let minY = height;
  let maxX = -1;
  let maxY = -1;
  let nearWhiteMinX = width;
  let nearWhiteMinY = height;
  let nearWhiteMaxX = -1;
  let nearWhiteMaxY = -1;
  const alphaAt = (x: number, y: number): number => (
    byteAt(pixels, (y * width + x) * 4 + 3, "alpha channel")
  );
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      const offset = (y * width + x) * 4;
      const alpha = byteAt(pixels, offset + 3, "alpha channel");
      if (alpha === 0) {
        transparentPixels += 1;
        continue;
      }
      nonTransparentPixels += 1;
      if (alpha >= 192) solidPixels += 1;
      else partialPixels += 1;
      if (
        byteAt(pixels, offset, "red channel") !== 255
        || byteAt(pixels, offset + 1, "green channel") !== 255
        || byteAt(pixels, offset + 2, "blue channel") !== 255
      ) {
        nonWhiteGlyphPixels += 1;
      }
      if (
        byteAt(pixels, offset, "red channel") >= 224
        && byteAt(pixels, offset + 1, "green channel") >= 224
        && byteAt(pixels, offset + 2, "blue channel") >= 224
      ) {
        nearWhitePixels += 1;
        if (x < 2 || y < 2 || x >= width - 2 || y >= height - 2) {
          nearWhiteOuterPixels += 1;
        }
        nearWhiteMinX = Math.min(nearWhiteMinX, x);
        nearWhiteMinY = Math.min(nearWhiteMinY, y);
        nearWhiteMaxX = Math.max(nearWhiteMaxX, x);
        nearWhiteMaxY = Math.max(nearWhiteMaxY, y);
      }
      minX = Math.min(minX, x);
      minY = Math.min(minY, y);
      maxX = Math.max(maxX, x);
      maxY = Math.max(maxY, y);
    }
  }

  return {
    width,
    height,
    cornerAlpha: [alphaAt(0, 0), alphaAt(width - 1, 0), alphaAt(0, height - 1), alphaAt(width - 1, height - 1)],
    transparentPixels,
    solidPixels,
    partialPixels,
    nonTransparentPixels,
    nonWhiteGlyphPixels,
    bounds: { minX, minY, maxX, maxY },
    nearWhitePixels,
    nearWhiteOuterPixels,
    nearWhiteBounds: {
      minX: nearWhiteMinX,
      minY: nearWhiteMinY,
      maxX: nearWhiteMaxX,
      maxY: nearWhiteMaxY,
    },
  };
}

describe("PWA manifest assets", () => {
  it("keeps any and maskable icons as separate install assets", () => {
    const parsed: unknown = JSON.parse(
      readFileSync(resolve(publicRoot, "manifest.webmanifest"), "utf8"),
    );
    const icons = manifestIcons(parsed);

    expect(icons).toEqual([
      { src: "/icons/app-icon-any-192-v2.png", sizes: "192x192", type: "image/png", purpose: "any" },
      { src: "/icons/app-icon-any-512-v2.png", sizes: "512x512", type: "image/png", purpose: "any" },
      { src: "/icons/app-icon-maskable-192.png", sizes: "192x192", type: "image/png", purpose: "maskable" },
      { src: "/icons/app-icon-maskable-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
    ]);
    expect(pngSize(resolve(publicRoot, "icons/app-icon-any-192-v2.png"))).toEqual({ width: 192, height: 192 });
    expect(pngSize(resolve(publicRoot, "icons/app-icon-any-512-v2.png"))).toEqual({ width: 512, height: 512 });
    expect(pngSize(resolve(publicRoot, "icons/app-icon-maskable-192.png"))).toEqual({ width: 192, height: 192 });
    expect(pngSize(resolve(publicRoot, "icons/app-icon-maskable-512.png"))).toEqual({ width: 512, height: 512 });
    expect(pngSize(resolve(publicRoot, "icons/notification-badge-96.png"))).toEqual({ width: 96, height: 96 });
  });

  it("keeps standard desktop icons transparent at their rounded corners", () => {
    for (const filename of ["app-icon-any-192-v2.png", "app-icon-any-512-v2.png"]) {
      const stats = rgbaPngAlphaStats(resolve(publicRoot, `icons/${filename}`));
      const totalPixels = stats.width * stats.height;

      expect(stats.cornerAlpha).toEqual([0, 0, 0, 0]);
      expect(stats.transparentPixels).toBeGreaterThan(0);
      expect(stats.transparentPixels).toBeLessThanOrEqual(Math.floor(totalPixels * 0.03));
      expect(stats.partialPixels).toBeGreaterThan(0);
      expect(stats.nonTransparentPixels).toBeGreaterThanOrEqual(Math.ceil(totalPixels * 0.95));
    }
  });

  it("uses dedicated transparent favicon assets instead of the cached install icon", () => {
    const index = readFileSync(resolve(publicRoot, "../index.html"), "utf8");
    const faviconAssets = [
      { filename: "favicon-16.png", size: 16 },
      { filename: "favicon-32.png", size: 32 },
    ] as const;

    expect(index).not.toContain('rel="icon" type="image/png" href="/icons/app-icon-512.png"');
    for (const { filename, size } of faviconAssets) {
      expect(index).toContain(
        `rel="icon" type="image/png" sizes="${size}x${size}" href="/icons/${filename}"`,
      );
      expect(pngSize(resolve(publicRoot, `icons/${filename}`))).toEqual({
        width: size,
        height: size,
      });

      const stats = rgbaPngAlphaStats(resolve(publicRoot, `icons/${filename}`));
      expect(stats.cornerAlpha).toEqual([0, 0, 0, 0]);
      expect(stats.transparentPixels).toBeGreaterThan(0);
      expect(stats.partialPixels).toBeGreaterThan(0);
      expect(stats.nearWhitePixels).toBeGreaterThan(0);
      expect(stats.nearWhiteOuterPixels).toBe(0);
      const minimumGlyphInset = Math.ceil(size * 0.18);
      expect(stats.nearWhiteBounds.minX).toBeGreaterThanOrEqual(minimumGlyphInset);
      expect(stats.nearWhiteBounds.minY).toBeGreaterThanOrEqual(minimumGlyphInset);
      expect(size - 1 - stats.nearWhiteBounds.maxX).toBeGreaterThanOrEqual(minimumGlyphInset);
      expect(size - 1 - stats.nearWhiteBounds.maxY).toBeGreaterThanOrEqual(minimumGlyphInset);
    }
  });

  it("keeps the Android notification badge as a centered monochrome alpha glyph", () => {
    const stats = rgbaPngAlphaStats(resolve(publicRoot, "icons/notification-badge-96.png"));
    const totalPixels = stats.width * stats.height;

    expect(stats.cornerAlpha).toEqual([0, 0, 0, 0]);
    expect(stats.transparentPixels).toBeGreaterThanOrEqual(Math.ceil(totalPixels * 0.6));
    expect(stats.nonTransparentPixels).toBeGreaterThanOrEqual(Math.ceil(totalPixels * 0.25));
    expect(stats.nonTransparentPixels).toBeLessThanOrEqual(Math.floor(totalPixels * 0.4));
    expect(stats.solidPixels).toBeGreaterThanOrEqual(Math.ceil(totalPixels * 0.2));
    expect(stats.partialPixels).toBeGreaterThan(0);
    expect(stats.nonWhiteGlyphPixels).toBe(0);
    expect(stats.bounds.minX).toBeGreaterThanOrEqual(12);
    expect(stats.bounds.minY).toBeGreaterThanOrEqual(12);
    expect(stats.width - 1 - stats.bounds.maxX).toBeGreaterThanOrEqual(12);
    expect(stats.height - 1 - stats.bounds.maxY).toBeGreaterThanOrEqual(12);
    expect(Math.abs(stats.bounds.minX - (stats.width - 1 - stats.bounds.maxX))).toBeLessThanOrEqual(1);
    expect(Math.abs(stats.bounds.minY - (stats.height - 1 - stats.bounds.maxY))).toBeLessThanOrEqual(1);
  });

  it("declares navigate-existing launch behavior and keyboard-resizing viewport support", () => {
    const manifest = readFileSync(resolve(publicRoot, "manifest.webmanifest"), "utf8");
    const index = readFileSync(resolve(publicRoot, "../index.html"), "utf8");

    expect(manifest).toContain('"id": "/"');
    expect(manifest).toContain('"scope": "/"');
    expect(manifest).toContain('"client_mode": "navigate-existing"');
    expect(index).toContain('/manifest.webmanifest?v=2');
    expect(index).toContain("interactive-widget=resizes-content");
    expect(index).toContain('/icons/apple-touch-icon-180.png');
    expect(pngSize(resolve(publicRoot, "icons/apple-touch-icon-180.png"))).toEqual({ width: 180, height: 180 });
  });
});
