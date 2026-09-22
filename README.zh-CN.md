# rust-docxtpl

[![CI](https://github.com/Anthony-Hoo/rust-docxtpl/actions/workflows/ci.yml/badge.svg)](https://github.com/Anthony-Hoo/rust-docxtpl/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/rust-docxtpl)](https://pypi.org/project/rust-docxtpl/)

[English](README.md) | 简体中文

`docxtpl`（python-docx-template）导入包的 Rust 加速发行版，可直接替换。
兼容基线：**docxtpl 0.20.1**（python-docx 1.2.0、Jinja2 3.1.x）。

```diff
- docxtpl==0.20.1
+ rust-docxtpl
```

```bash
pip uninstall docxtpl && pip install rust-docxtpl
```

预编译 wheel 覆盖 CPython 3.10–3.14 的 Linux（x86_64、aarch64，manylinux2014）、
Windows（x86_64）和 macOS（arm64、x86_64）。其他平台从 sdist 构建，需要 Rust
工具链（`cargo` 在 `PATH` 上时 `pip install rust-docxtpl` 会自动编译）。

应用代码、模板和上下文数据一行不改：

```python
from docxtpl import DocxTemplate, InlineImage, RichText

tpl = DocxTemplate(path)
tpl.get_undeclared_template_variables()
tpl.render(context, jinja_env)
tpl.save(output)
```

Jinja2 求值仍由真实的 Jinja2 用你的 `Environment` 完成（过滤器、测试、全局变量、
`Undefined`、autoescape、上下文里的 Python 对象都照旧），文档仍是真实的
python-docx / lxml 对象，`render()` 前后都可以修改。**不要**和原版 `docxtpl`
发行包同时安装：两者都拥有 `docxtpl` 这个包目录，混装时 `import docxtpl` 会抛出
带说明的 `ImportError`。

## 快在哪里，为什么

在作者能拿到的最大真实模板上测量（`document.xml` 3.7 MB、14.1 万个元素），
见[基准测试](#基准测试)。

| 上游开销 | 原因 | 本包的做法 |
|---|---|---|
| `render()` 约 88% 的时间在 `map_tree()` | `root.replace(body, tree)` 让 lxml 逐个重挂*旧*正文的每个节点。对根元素上声明的命名空间，lxml 的命名空间缓存永远不命中（它存的是 `(new, new)` 而不是 `(old, new)`，见 `proxy.pxi:_fixCNs`），工作量随正文大小平方增长 | 把渲染后的正文挂到根元素的一个孪生副本上，再把 `Document._element` / `DocumentPart._element` 指过去。什么都不拆，旧树保持完整，和上游留下的状态一样 |
| `patch_xml()`：约 20 趟回溯正则扫全文，每个部件、每次扫描、每次 render 都跑 | Python `re` 加环视 | 手写 Rust 扫描器（`crates/core`），逐字节等价，执行时释放 GIL |
| 每次 `get_undeclared_template_variables()` 都重新加载、patch、解析模板 | 没有复用 | 以模板字节的 SHA-256 加环境指纹为键缓存结果 |
| Jinja2 每次 render 都重新 lex、parse、compile 数 MB 的源码 | `from_string` 没有缓存 | *等价*环境之间共享编译后的代码对象 |

## 兼容性契约

输出用严格的 OOXML 比较器与上游对比（全部部件、元素顺序、属性、命名空间绑定、
文本、尾文本、关系 id、媒体字节）。

| API | 状态 |
|---|---|
| `DocxTemplate(path / PathLike / stream)`、`render`、`save`、`init_docx`、`get_docx`、`.docx`、属性代理 | 与上游同一条代码路径 |
| `patch_xml(str) -> str` | 原生实现；逐字节相同（差异模糊测试 + 35 份真实模板） |
| `get_undeclared_template_variables(jinja_env=None, context=None)` | 有缓存；始终分析模板*文件*，返回新的 `set`，恢复流的读取位置 |
| `InlineImage`（可继承，`_insert_image`、`_add_hyperlink`）、`RichText`/`R`、`RichTextParagraph`/`RP`、`Listing`、`Subdoc`、`new_subdoc(path)` | 上游代码，原样 |
| `replace_pic/media/embedded/zipname`、`reset_replacements`、`build_url_id`、`python -m docxtpl` | 上游代码，原样 |
| 子类覆盖 `patch_xml`、`xml_to_string`、`resolve_listing`、`map_tree` 等 | 生效；相应的缓存 / 原生快路径自动绕过 |

有意为之的差异：

1. `render()` 之后 `tpl.docx._element` 是一个新的根元素对象，除非有其他对象引用着
   旧根（那时走上游的慢路径，保持身份不变）。`Document`、`DocumentPart`、关系和
   其他所有部件的身份都不变。`render()` *之前*取到的对象（包括 python-docx 缓存的
   `document._body`）保持完整但陈旧，和上游完全一样；只是它们的 `getparent()` 链
   终止在旧根而不是旧正文。
2. 在同一个实例上重入 `render()` 抛 `RuntimeError`。
3. *不纯*且*只作用于常量*的自定义过滤器仍会在每次 render 时求值（这类模板永远不
   走代码缓存）；用户无需处理。

### 缓存

进程内 LRU，按字节预算（`DOCXTPL_CACHE_BYTES`，默认 32 MiB，`0` 关闭；或
`docxtpl.set_cache_budget()`），以内容摘要为键，从不用路径、mtime 或 `id()`。
只存变量名集合、patch 后的模板 XML 和编译后的代码对象：从不存上下文、图片、
渲染结果或回调结果。每次调用都重新计算环境指纹（环境是可变的）；`Environment`
的子类、扩展、`finalize` 钩子、可调用的 `autoescape`，以及是闭包、绑定方法或
可调用对象的过滤器 / 测试，都会关闭对应的缓存层。

编译后的代码还可以通过磁盘缓存在*进程之间*共享：设置
`DOCXTPL_CODE_CACHE_DIR=/path`（或调用
`docxtpl.configure_code_cache(path, max_entries=512)`）。pre-fork 服务器的
worker 会被轮换，否则几乎每个请求都要重新 lex、parse、compile 数 MB 的源码。
条目是 `marshal` 序列化的代码对象，键由源码摘要、环境的稳定描述（自定义过滤器 /
测试的名字和代码、`undefined`、policies、词法设置）以及 Python / Jinja2 / marshal
版本组成；损坏的条目会被丢弃，目录大小由 `DOCXTPL_CODE_CACHE_MAX_ENTRIES` 限制。
与 `jinja2.FileSystemBytecodeCache` 一样，该目录里的代码会被执行，因此只能由
应用自己写入。默认关闭。

### 可观测性

```python
import docxtpl
docxtpl.enable_timings()          # 或 DOCXTPL_TIMINGS=1
...
docxtpl.stats()
# {'counters': {'patch_xml_native': 19, 'patch_xml_reference': 0, 'map_tree_swap': 1,
#               'map_tree_replace': 0, 'jinja_compile_reused': 19, 'cache_code_hit': 19, ...},
#  'timings': {'render': {'calls': 1, 'seconds': 0.17}, ...}}
docxtpl.cache_info()
```

`*_reference` / `map_tree_replace` / `jinja_compile_plain` 统计的是与上游等价的
慢路径的执行次数。不会记录任何模板文本或上下文数据。

### 可选：python-docx XPath 缓存（`docxtpl.accel`）

默认关闭。`docxtpl.accel.enable()`（或 `DOCXTPL_ACCEL=1`）只替换一个方法
`BaseOxmlElement.xpath`，用复用编译后表达式的等价实现代替每次调用都新建 lxml
求值器。在最大的报告上测得：读取全部单元格段落文本 0.24 s → 0.085 s；表格归一化
和 docxcompose 合并不变（它们不受 XPath 制约）。对完整导出的预期收益只有几个
百分点，所以做成可选项；这也是**不**重写 python-docx 的原因：它的对象*就是*
lxml 元素，应用和 docxcompose 直接操作它们，解析 / XPath / 序列化本来就在 C 里跑，
给 `qn()` 加缓存实测没有收益。

## 目录结构

```
crates/core   纯 Rust 内核（不依赖 Python）：patch.rs、render.rs、scan.rs
crates/py     PyO3 绑定 -> docxtpl._native
python/docxtpl
  template.py      上游 DocxTemplate，热点路径改道
  _reference.py    上游正则代码，原样：回退路径 + 测试基准
  _jinja.py        环境指纹、编译代码复用
  _cache.py        字节预算 LRU          _stats.py  计数器 / 计时
  accel.py         可选的 python-docx XPath 缓存
tests/        pytest：差异测试（模糊测试 + 可选的私有语料）、门面行为
.github/      ci.yml（clippy、cargo test、三个 OS 上的 pytest）、release.yml（wheel -> PyPI）
```

每一个 Rust pass 都引用它替换的那条正则，并写明实现的匹配规则。规则有疑问时，
`_reference.py` 是规格，`tests/fuzz.py` 是裁判：

```bash
python -m venv .venv && . .venv/bin/activate && pip install maturin pytest
maturin develop --release                      # 把 docxtpl._native 构建进 .venv
cargo test && cargo clippy --all-targets -- -D warnings
pytest tests                                   # 依赖私有 DOCX 语料的差异测试
                                               # 在语料缺失时自动跳过
python tests/fuzz.py 500000 7                  # 对 _reference.py 做语法模糊测试
python tests/make_golden.py                    # 修改模糊语法 / 参考实现后重新生成
```

## 基准测试

（私有的）基准框架交替启动两个环境的新进程（AB/BA）；每个进程的第 1 次迭代是
*冷*（缓存为空），之后是*热*。
下面的数字来自 13 代 Core i9 笔记本、WSL2、CPython 3.12.14、lxml 5.3.1；
库层 p50 = 两次变量扫描 + `render()`。模板是私有的生产文档（一份 14.1 万元素的
测试报告、六份原始记录模板、一份封面），不在本仓库中；数字仅供参考。

A = `.venv-baseline`，B = `.venv-candidate`；4 轮 × 每进程 3 次迭代。

| 用例 | 库层 A p50 | B 冷 p50 | B 热 p50 | 冷 B/A | 热 B/A | 热 p95 B/A | 热 CPU B/A | 冷峰值 RSS B/A |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| remote-report-tpl194 | 13.047 s | 0.829 s | 0.166 s | 0.063 | 0.013 | 0.013 | 0.020 | 0.77 |
| remote-records-tpl177 | 1.213 s | 0.190 s | 0.027 s | 0.156 | 0.022 | 0.023 | 0.050 | 0.74 |
| remote-records-tpl178 | 0.280 s | 0.075 s | 0.009 s | 0.265 | 0.032 | 0.034 | 0.066 | 0.95 |
| remote-records-tpl180 | 1.312 s | 0.188 s | 0.029 s | 0.143 | 0.022 | 0.027 | 0.047 | 0.82 |
| remote-records-tpl181 | 0.383 s | 0.076 s | 0.007 s | 0.192 | 0.019 | 0.019 | 0.037 | 0.81 |
| remote-records-tpl183 | 0.758 s | 0.128 s | 0.019 s | 0.168 | 0.026 | 0.029 | 0.046 | 0.88 |
| remote-records-tpl185 | 0.028 s | 0.013 s | 0.002 s | 0.419 | 0.078 | 0.082 | 0.456 | 0.89 |
| local-cover-tpl185 | 0.029 s | 0.013 s | 0.002 s | 0.411 | 0.073 | 0.077 | 0.446 | 0.89 |
| synthetic-object-protocol | 0.017 s | 0.014 s | 0.003 s | 0.665 | 0.149 | 0.126 | 0.469 | 1.00 |

对照组：报告模板，同样的门面和算法，但关闭 Rust 内核（`DOCXTPL_NATIVE=0`）：
库层冷 2.63 s / 热 1.10 s，开启时 0.83 s / 0.17 s。也就是说，纯 Python 优化之后
剩下的时间里，Rust 内核又去掉了 68% / 85%。

## 构建 wheel

`maturin build --release` 生成本机平台的 wheel。
`.github/workflows/release.yml` 在每个 `v*` tag 上构建完整的 wheel 矩阵和 sdist，
并通过 [trusted publishing](https://docs.pypi.org/trusted-publishers/) 发布到
PyPI（仓库里不存任何 API token）；`ci.yml` 在每次 push 和 pull request 时于
Linux、Windows、macOS 上运行 clippy、`cargo test` 和 pytest。

## 许可证

LGPL-2.1-only，作为 docxtpl 的衍生作品。见 `LICENSE` 与 `NOTICE`。
