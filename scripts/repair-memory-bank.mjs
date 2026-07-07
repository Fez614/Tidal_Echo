import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const memoryDir = path.join(root, "memory_bank");
const sourceFile = path.join(memoryDir, "memory_import_seed.md");
const targetFile = path.join(memoryDir, "memories.json");

const categoryMap = new Map([
  ["core relationship", { type: "relationship", scope: "core", tags: ["relationship"], importance: 5 }],
  ["personal facts", { type: "fact", scope: "long_term", tags: ["fact"], importance: 3 }],
  ["preferences", { type: "preference", scope: "long_term", tags: ["preference"], importance: 3 }],
  ["emotional notes", { type: "emotion", scope: "long_term", tags: ["emotion"], importance: 3 }],
  ["style samples", { type: "style", scope: "style_sample", tags: ["style"], importance: 3 }],
  ["rules", { type: "rule", scope: "core", tags: ["rule"], importance: 5 }],
]);

function idFor(type, content) {
  return `${type.slice(0, 3)}-${crypto.createHash("sha1").update(`${type}\n${content}`).digest("hex").slice(0, 10)}`;
}

function fieldsFor(heading) {
  return categoryMap.get(heading.trim().toLowerCase()) || categoryMap.get("personal facts");
}

const now = new Date().toISOString();
const markdown = fs.readFileSync(sourceFile, "utf8");
const seen = new Set();
const memories = [];
let heading = "personal facts";

for (const rawLine of markdown.split(/\r?\n/)) {
  let line = rawLine.trim();
  if (!line) continue;
  if (line.startsWith("#")) {
    heading = line.replace(/^#+/, "").trim();
    continue;
  }
  if (/^[-*•]\s+/.test(line)) line = line.replace(/^[-*•]\s+/, "").trim();
  if (!line) continue;

  const fields = fieldsFor(heading);
  const key = `${fields.type}\u0000${line}`;
  if (seen.has(key)) continue;
  seen.add(key);

  memories.push({
    id: idFor(fields.type, line),
    content: line,
    type: fields.type,
    scope: fields.scope,
    tags: fields.tags,
    importance: fields.importance,
    emotion: {},
    source: "memory_import_seed.md",
    status: "active",
    evidence: "",
    createdAt: now,
    updatedAt: now,
    lastAccessedAt: "",
  });
}

fs.mkdirSync(memoryDir, { recursive: true });
if (fs.existsSync(targetFile)) {
  const backup = path.join(memoryDir, `memories.backup-${Date.now()}.json`);
  fs.copyFileSync(targetFile, backup);
}
fs.writeFileSync(targetFile, `${JSON.stringify(memories, null, 2)}\n`, "utf8");
console.log(`wrote ${memories.length} memories`);
