export const DEMO_PERSONA_NAME = "林屿的证据分身";

export const DEMO_PERSONA_DESCRIPTION =
  "一个只依据已审核资料回答的本地演示人物。所有内容均为虚构示例。";

export const DEMO_FILENAME = "personaos-demo-journal.md";

export const DEMO_SOURCE = `# 林屿的项目日记（虚构演示）

2026-06-18，我开始参与开源项目“潮汐笔记”，主要负责资料导入和可追溯引用。

我做技术方案时习惯先写结论，再列证据与限制。遇到无法从资料确认的内容，我希望明确说“不确定”，而不是补全一个听起来合理的答案。

工作日上午我通常先处理代码审查，下午再安排需要长时间专注的架构设计。

我偏好本地优先的软件。涉及私人日记时，只允许使用本机模型；未经单独确认，不应把原文发送给外部模型服务。
`;

export function demoFile(): File {
  return new File([DEMO_SOURCE], DEMO_FILENAME, {
    type: "text/markdown",
  });
}
