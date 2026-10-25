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

## 验证

本地只做源码静态检查、shell 语法检查和差异检查，不运行测试、构建、编译或冒烟脚本。
回归测试和真实的 macOS arm64 打包、挂载、内置 SDK、WebKit 验证由现有 GitHub Actions 执行。
远端结果待运行后补充；可选真实提供商验收不属于此次打包修复的验证范围。
