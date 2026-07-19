/**
 * HC-201 — Parser Registry (no hard-coded single parser path).
 * Parsers register themselves; Import Engine resolves by document type / content.
 */
(function (global) {
  "use strict";

  const registry = new Map();

  /**
   * @typedef {{
   *   id: string,
   *   name: string,
   *   version: string,
   *   supportedTypes: string[],
   *   canParse: (ctx: object) => boolean,
   *   parse: (ctx: object) => Promise<{measurements: object[], confidence: number, notes?: string[]}> | {measurements: object[], confidence: number, notes?: string[]}
   * }} HealthParser
   */

  function register(parser) {
    if (!parser || !parser.id) throw new Error("Parser must have an id");
    registry.set(parser.id, parser);
    return parser.id;
  }

  function unregister(id) {
    return registry.delete(id);
  }

  function list() {
    return Array.from(registry.values());
  }

  function get(id) {
    return registry.get(id) || null;
  }

  function resolve(ctx) {
    const candidates = list().filter((p) => {
      try {
        return typeof p.canParse === "function" ? p.canParse(ctx) : false;
      } catch (_) {
        return false;
      }
    });
    // Prefer higher priority if provided
    candidates.sort((a, b) => (b.priority || 0) - (a.priority || 0));
    return candidates[0] || null;
  }

  async function parseWithRegistry(ctx) {
    const parser = resolve(ctx);
    if (!parser) {
      return {
        parser: null,
        measurements: [],
        confidence: 0,
        notes: ["No registered parser matched this document"],
      };
    }
    const result = await Promise.resolve(parser.parse(ctx));
    return {
      parser: { id: parser.id, name: parser.name, version: parser.version },
      measurements: (result && result.measurements) || [],
      confidence: result && result.confidence != null ? Number(result.confidence) : 0,
      notes: (result && result.notes) || [],
    };
  }

  global.HCParserRegistry = {
    register,
    unregister,
    list,
    get,
    resolve,
    parseWithRegistry,
  };
})(typeof window !== "undefined" ? window : globalThis);
