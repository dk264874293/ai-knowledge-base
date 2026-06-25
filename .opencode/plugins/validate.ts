import path from "node:path";
import type { Plugin } from "@opencode-ai/plugin";

const TARGET_TOOLS = new Set(["write", "edit"]);
const ARTICLES_DIR = path.join("knowledge", "articles");

export const ValidatePlugin: Plugin = async ({ directory, worktree, $ }) => {
  const baseDir = worktree || directory;

  return {
    "tool.execute.after": async (input, output) => {
      if (!TARGET_TOOLS.has(input.tool)) return;

      const args = (input.args ?? {}) as Record<string, unknown>;
      const rawPath = args.file_path ?? args.filePath;
      if (typeof rawPath !== "string" || rawPath.length === 0) return;

      const resolved = path.isAbsolute(rawPath)
        ? rawPath
        : path.resolve(baseDir, rawPath);
      const rel = path.relative(baseDir, resolved);

      const inArticles =
        (rel === ARTICLES_DIR || rel.startsWith(ARTICLES_DIR + path.sep)) &&
        rel.toLowerCase().endsWith(".json");
      if (!inArticles) return;

      try {
        // NOTE: .nothrow() (not .quiet()) — .quiet() deadlocks OpenCode.
        // .nothrow() lets us read exitCode/output on validation failure
        // instead of throwing. try/catch still guards against spawn errors.
        const result = await $`python3 hooks/validate_json.py ${resolved}`
          .cwd(baseDir)
          .nothrow();
        const detail = result.text().trim();
        const label = path.relative(baseDir, resolved);

        if (result.exitCode === 0) {
          output.output = `${output.output ?? ""}\n\n[validate] ${label} PASS`.trim();
        } else {
          output.title = "JSON 校验失败";
          output.output =
            `${output.output ?? ""}\n\n[validate] ${label} FAIL (exit ${result.exitCode})\n${detail}`.trim();
        }
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        output.title = "校验脚本执行异常";
        output.output =
          `${output.output ?? ""}\n\n[validate] 执行异常: ${msg}`.trim();
      }
    },
  };
};
