import { afterEach, vi } from "vitest";
import { cleanup } from "@testing-library/react";

afterEach(() => cleanup());

Object.defineProperty(window, "scrollTo", { value: vi.fn(), writable: true });
Object.defineProperty(window, "open", { value: vi.fn(), writable: true });
