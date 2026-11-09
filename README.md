# Pulse 扩展仓库 · Pulse Extensions

Pulse 文件管理器「设置 › 增强包」里的商店从这里读取扩展目录。每个扩展都经过审核，安装包有发布者签名，Pulse 下载后逐项核对大小、SHA-256 和签名，全部一致才安装。

The store in Pulse (Settings › 增强包) reads its catalog from this repository. Every extension is reviewed; packages are signed by their publisher, and Pulse checks size, SHA-256 and signature before installing.

## 工作方式 · How it works

```
publishers/<fingerprint>.json   发布者与公钥（审核后合并）       publisher + public key
extensions/<id>/extension.json  扩展信息与各版本下载地址          extension + released versions
tools/catalog.py                校验目录、生成并发布签名索引      validate, build and publish
tools/pulse_sign.py             生成密钥、签名清单与目录          keys and signatures
```

1. 作者在自己的 GitHub 仓库发布 Release，附上 `.pulse-ext` 安装包和 `.sig.json` 签名。
2. 作者向本仓库提交 PR，新增或更新 `extensions/<id>/extension.json`（首次发布再加 `publishers/<指纹>.json`）。
3. CI 下载安装包，核对大小、哈希、清单、x64 DLL 和签名。
4. 维护者审核合并后运行 `tools/catalog.py publish`，用 Pulse 官方密钥签名 `index.json`，上传到 [`catalog` Release](https://github.com/jimmgreen/pulse-extensions/releases/tag/catalog)。
5. Pulse 通过与增强包相同的下载源（镜像 1、镜像 2、GitHub）读取 `index.json` 和 `index.sig.json`，用内置的官方公钥验证。

Authors publish a GitHub release with the `.pulse-ext` package and `.sig.json` signature, then open a pull request here. CI verifies the download; after review the maintainer signs and uploads `index.json` to the `catalog` release, which Pulse fetches through its usual download sources and verifies with the pinned official key.

## 发布扩展 · Publishing

见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 安全 · Security

原生扩展是在 Pulse 进程内运行的 DLL，拥有与 Pulse 相同的权限。审核关注来源可追溯、行为与说明一致、不联网上传数据、写文件经过 Pulse 的确认流程。发现问题请提交 issue 并标注 `security`，维护者会从目录下架该版本；已安装的副本在 Pulse 下次检查目录时提示停用。

Native extensions are DLLs that run inside the Pulse process with its privileges. Report a problem with the `security` label; the maintainer removes the version from the catalog and Pulse offers to disable installed copies on its next catalog check.
