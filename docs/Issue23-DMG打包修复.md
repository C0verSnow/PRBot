# Issue #23：修复安装包打包失败

## 报错和原因

[失败运行 40169500880](https://github.com/C0verSnow/PRBot/actions/runs/40169500880)
的 test 已通过，dmg 在执行 `scripts/build_dmg.sh` 时失败：

```text
cp: 使用手册.md: No such file or directory
Process completed with exit code 1.
```

提交 `94b3094` 删除了使用手册，但打包脚本还在复制它。失败发生时，PyInstaller
和应用签名已经完成；安装包尚未生成。README 已有安装、配置和使用说明，改为把它放进安装包。

## 修改

- 构建脚本先检查 `README.md` 和 `.env.example`，缺少时直接指出文件名，在调用 PyInstaller 前停止。
- 安装包附带 `README.md`、`.env.example`，开发版提示文字明确指向 README。
- 远端安装包验证挂载 DMG 后，逐字节对比两份文件与当前仓库内容，防止漏放或放错版本。
- Actions 下载产物附带 README 和本文，移除已不存在的手册与验收记录路径。
- 三条回归测试在临时目录运行真实构建脚本，只替换构建、签名和磁盘工具：验证文档确实被复制，
  以及 README 或配置模板缺失时在构建前失败。临时目录带空格，同时覆盖脚本路径的引用处理。
  首轮远端测试还发现 macOS Bash 会把紧挨变量的中文标点当成变量名的一部分，已用 `${required_file}` 明确边界。

## 验证

本地只做源码静态检查、shell 语法检查和差异检查，不运行测试、构建、编译或冒烟脚本。
回归测试和真实的 macOS arm64 打包、挂载、内置 SDK、WebKit 验证由现有 GitHub Actions 执行。
修复提交 `e8b2d80325a937bd6df6df2c7a45c02e743df6c2` 已通过
[Actions 40173202524](https://github.com/C0verSnow/PRBot/actions/runs/40173202524)：

- 312 项 Python 测试通过，含三条打包回归；Ruff、pip check、TypeScript 和前端构建通过。
- 源码与打包后的 WebKit 检查通过。
- DMG 构建、校验和、挂载复制、README / 配置模板逐字节比对、内置 SDK 初始化通过。
- [DMG 下载](https://github.com/C0verSnow/PRBot/actions/runs/40173202524/artifacts/12591580055)
  · [测试结果](https://github.com/C0verSnow/PRBot/actions/runs/40173202524/artifacts/12590833903)
  · [打包界面证据](https://github.com/C0verSnow/PRBot/actions/runs/40173202524/artifacts/12590734301)。

[PR #24](https://github.com/C0verSnow/PRBot/pull/24) 保持打开供 review；
[issue #23](https://github.com/C0verSnow/PRBot/issues/23) 按要求在验证通过后关闭。
后续只补交本文和 tasklist 的证据记录，不再改动脚本和测试。
可选真实提供商验收未运行，不属于此次打包修复的验证范围。
