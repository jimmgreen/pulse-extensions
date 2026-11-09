# 发布扩展 · Publishing an extension

## 1. 构建 · Build

用 Pulse 原生 SDK（`sdk/native`，当前为 preview 3）构建 x64 DLL，参考 [`sdk/native/samples/archive`](https://github.com/jimmgreen/pulse/tree/main/sdk/native/samples/archive)。然后在 DLL 旁生成清单并打包：

```powershell
python tools/extensions/pulse_ext.py --pulse build/pulse.exe --json native-manifest manifest.json --library build/plugin.dll --output out/manifest.json
python tools/extensions/pulse_ext.py --pulse build/pulse.exe --json pack out/manifest.json --output out/my.ext-1.0.0.pulse-ext
```

（这两条命令在 Pulse 主仓库里运行。）

## 2. 签名 · Sign

第一次发布先生成发布者密钥。**私钥永远不要提交到任何仓库。**

```powershell
python tools/pulse_sign.py keygen --out-dir %LOCALAPPDATA%\PulseSigning\my-name
python tools/pulse_sign.py sign-manifest out/manifest.json --private-key %LOCALAPPDATA%\PulseSigning\my-name\publisher.private.blob --output out/my.ext-1.0.0.sig.json
```

签名针对清单的原始字节；重新生成清单后必须重新签名。

## 3. 发布 Release · Release

在你自己的 GitHub 仓库创建 Release，上传 `.pulse-ext` 和 `.sig.json`。下载地址必须是 `https://github.com/<owner>/<repo>/releases/download/<tag>/<file>` 形式，国内镜像只加速这种地址。

## 4. 提交 PR · Pull request

- 首次发布：新增 `publishers/<指纹>.json`，内容是 `keygen` 输出的 `publisher` 与 `publicKey`。
- 新增或更新 `extensions/<id>/extension.json`，版本按新到旧排列。字段见下方示例和 `tools/catalog.py`。
- 本地先运行 `python tools/catalog.py validate --download --only <id>`。

`pulse.*` 命名空间保留给官方扩展。`id` 必须与清单中的 `id` 一致，文件夹名同 `id`。

```json
{
  "formatVersion": 1,
  "id": "example.rename-rules",
  "publisher": "<64 位小写指纹>",
  "name": { "zh": "批量重命名规则库", "en": "Rename rules" },
  "summary": { "zh": "一句话说明", "en": "One line" },
  "description": { "zh": "详细说明", "en": "Details" },
  "category": "fileops",
  "icon": { "glyph": "rename", "color": "#0F766E" },
  "license": "MIT",
  "source": "https://github.com/you/rename-rules",
  "versions": [{
    "version": "1.0.0",
    "released": "2026-11-09",
    "minPulse": "1.0.60",
    "sdkPreview": 3,
    "manifestSha256": "<清单 SHA-256>",
    "resources": ["settings.own"],
    "package": { "url": "https://github.com/you/rename-rules/releases/download/v1.0.0/example.rename-rules-1.0.0.pulse-ext", "size": 123456, "sha256": "<...>" },
    "signature": { "url": "https://github.com/you/rename-rules/releases/download/v1.0.0/example.rename-rules-1.0.0.sig.json", "size": 812, "sha256": "<...>" },
    "notes": { "zh": "首个版本", "en": "First release" }
  }]
}
```

分类：`fileops` 文件操作、`preview` 预览、`search` 搜索、`panel` 面板、`tools` 工具。图标从 `archive film image doc find ocr git rename duplicate compress cloud puzzle` 中选一个，颜色用大写 `#RRGGBB`。

## 5. 审核 · Review

维护者会检查：源码或构建说明可获取；行为与说明一致；不上传用户数据；写入文件通过 Pulse 的确认与暂存流程；许可证与第三方声明齐全。合并后维护者发布新的签名目录，Pulse 在下次检查时看到新版本。

Maintainers check that the source or build instructions are available, behavior matches the description, no user data is uploaded, file writes go through Pulse's confirmation and staging, and licenses are included. After merge the maintainer publishes a newly signed catalog.
