"""File-to-language mapping, colors and exclusion rules (subset of GitHub Linguist)."""

import fnmatch
import posixpath

# language -> (color, kind)   kind: "code" | "markup" | "data" | "prose"
LANGUAGES = {
    "Assembly": ("#6E4C13", "code"),
    "Astro": ("#ff5a03", "markup"),
    "Batchfile": ("#C1F12E", "code"),
    "C": ("#555555", "code"),
    "C#": ("#178600", "code"),
    "C++": ("#f34b7d", "code"),
    "Clojure": ("#db5855", "code"),
    "CMake": ("#DA3434", "code"),
    "CSS": ("#663399", "markup"),
    "CSV": ("#237346", "data"),
    "Dart": ("#00B4AB", "code"),
    "Dockerfile": ("#384d54", "code"),
    "Elixir": ("#6e4a7e", "code"),
    "Erlang": ("#B83998", "code"),
    "F#": ("#b845fc", "code"),
    "GDScript": ("#355570", "code"),
    "GLSL": ("#5686a5", "code"),
    "Go": ("#00ADD8", "code"),
    "GraphQL": ("#e10098", "data"),
    "Groovy": ("#4298b8", "code"),
    "Haskell": ("#5e5086", "code"),
    "HCL": ("#844FBA", "code"),
    "HLSL": ("#aace60", "code"),
    "HTML": ("#e34c26", "markup"),
    "Java": ("#b07219", "code"),
    "JavaScript": ("#f1e05a", "code"),
    "JSON": ("#292929", "data"),
    "Julia": ("#a270ba", "code"),
    "Jupyter Notebook": ("#DA5B0B", "code"),
    "Kotlin": ("#A97BFF", "code"),
    "LaTeX": ("#3D6117", "markup"),
    "Less": ("#1d365d", "markup"),
    "Lua": ("#000080", "code"),
    "Makefile": ("#427819", "code"),
    "Markdown": ("#083fa1", "prose"),
    "MDX": ("#fcb32c", "prose"),
    "Nim": ("#ffc200", "code"),
    "Nix": ("#7e7eff", "code"),
    "Objective-C": ("#438eff", "code"),
    "OCaml": ("#ef7a08", "code"),
    "Perl": ("#0298c3", "code"),
    "PHP": ("#4F5D95", "code"),
    "PowerShell": ("#012456", "code"),
    "Prisma": ("#0c344b", "data"),
    "Protocol Buffer": ("#555555", "data"),
    "Python": ("#3572A5", "code"),
    "R": ("#198CE7", "code"),
    "Ruby": ("#701516", "code"),
    "Rust": ("#dea584", "code"),
    "Sass": ("#a53b70", "markup"),
    "Scala": ("#c22d40", "code"),
    "SCSS": ("#c6538c", "markup"),
    "ShaderLab": ("#222c37", "code"),
    "Shell": ("#89e051", "code"),
    "Solidity": ("#AA6746", "code"),
    "SQL": ("#e38c00", "data"),
    "Svelte": ("#ff3e00", "markup"),
    "Swift": ("#F05138", "code"),
    "TOML": ("#9c4221", "data"),
    "TypeScript": ("#3178c6", "code"),
    "Visual Basic .NET": ("#945db7", "code"),
    "Vue": ("#41b883", "markup"),
    "XML": ("#0060ac", "data"),
    "YAML": ("#cb171e", "data"),
    "Zig": ("#ec915c", "code"),
}

EXTENSIONS = {
    ".asm": "Assembly", ".s": "Assembly", ".nasm": "Assembly",
    ".astro": "Astro",
    ".bat": "Batchfile", ".cmd": "Batchfile",
    ".c": "C", ".h": "C",
    ".cs": "C#", ".csx": "C#",
    ".cpp": "C++", ".cc": "C++", ".cxx": "C++", ".hpp": "C++", ".hh": "C++", ".hxx": "C++", ".ino": "C++",
    ".clj": "Clojure", ".cljs": "Clojure", ".cljc": "Clojure", ".edn": "Clojure",
    ".cmake": "CMake",
    ".css": "CSS",
    ".csv": "CSV",
    ".dart": "Dart",
    ".ex": "Elixir", ".exs": "Elixir",
    ".erl": "Erlang", ".hrl": "Erlang",
    ".fs": "F#", ".fsi": "F#", ".fsx": "F#",
    ".gd": "GDScript",
    ".glsl": "GLSL", ".vert": "GLSL", ".frag": "GLSL", ".geom": "GLSL", ".comp": "GLSL",
    ".go": "Go",
    ".graphql": "GraphQL", ".gql": "GraphQL",
    ".groovy": "Groovy", ".gradle": "Groovy",
    ".hs": "Haskell", ".lhs": "Haskell",
    ".tf": "HCL", ".hcl": "HCL", ".tfvars": "HCL",
    ".hlsl": "HLSL", ".cginc": "HLSL", ".compute": "HLSL", ".fx": "HLSL",
    ".html": "HTML", ".htm": "HTML", ".xhtml": "HTML",
    ".java": "Java",
    ".js": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript", ".jsx": "JavaScript",
    ".json": "JSON", ".jsonc": "JSON", ".json5": "JSON",
    ".jl": "Julia",
    ".ipynb": "Jupyter Notebook",
    ".kt": "Kotlin", ".kts": "Kotlin",
    ".tex": "LaTeX", ".sty": "LaTeX", ".cls": "LaTeX",
    ".less": "Less",
    ".lua": "Lua",
    ".mk": "Makefile",
    ".md": "Markdown", ".markdown": "Markdown",
    ".mdx": "MDX",
    ".nim": "Nim",
    ".nix": "Nix",
    ".m": "Objective-C", ".mm": "Objective-C",
    ".ml": "OCaml", ".mli": "OCaml",
    ".pl": "Perl", ".pm": "Perl",
    ".php": "PHP",
    ".ps1": "PowerShell", ".psm1": "PowerShell", ".psd1": "PowerShell",
    ".prisma": "Prisma",
    ".proto": "Protocol Buffer",
    ".py": "Python", ".pyi": "Python", ".pyw": "Python",
    ".r": "R",
    ".rb": "Ruby", ".rake": "Ruby", ".gemspec": "Ruby",
    ".rs": "Rust",
    ".sass": "Sass",
    ".scala": "Scala", ".sc": "Scala",
    ".scss": "SCSS",
    ".shader": "ShaderLab",
    ".sh": "Shell", ".bash": "Shell", ".zsh": "Shell", ".fish": "Shell",
    ".sol": "Solidity",
    ".sql": "SQL",
    ".svelte": "Svelte",
    ".swift": "Swift",
    ".toml": "TOML",
    ".ts": "TypeScript", ".mts": "TypeScript", ".cts": "TypeScript", ".tsx": "TypeScript",
    ".vb": "Visual Basic .NET",
    ".vue": "Vue",
    ".xml": "XML", ".csproj": "XML", ".props": "XML", ".targets": "XML", ".xaml": "XML", ".plist": "XML",
    ".yml": "YAML", ".yaml": "YAML",
    ".zig": "Zig",
}

FILENAMES = {
    "dockerfile": "Dockerfile", "containerfile": "Dockerfile",
    "makefile": "Makefile", "gnumakefile": "Makefile",
    "cmakelists.txt": "CMake",
    "gemfile": "Ruby", "rakefile": "Ruby",
    "justfile": "Makefile",
}

# Vendored, generated or binary-ish files that would distort the statistics.
EXCLUDE_PATTERNS = [
    "*/node_modules/*", "*/vendor/*", "*/third_party/*", "*/thirdparty/*", "*/external/*",
    "*/dist/*", "*/build/*", "*/out/*", "*/bin/*", "*/obj/*", "*/target/*", "*/.next/*",
    "*/coverage/*", "*/__pycache__/*", "*/.venv/*", "*/venv/*", "*/site-packages/*",
    "*/Library/*", "*/Temp/*", "*/Logs/*",  # Unity
    "*.min.js", "*.min.css", "*.bundle.js", "*.map", "*.generated.*", "*.g.cs", "*.designer.cs",
    "*/package-lock.json", "*/yarn.lock", "*/pnpm-lock.yaml", "*/bun.lockb", "*/composer.lock",
    "*/Cargo.lock", "*/poetry.lock", "*/Pipfile.lock", "*/Gemfile.lock", "*/go.sum", "*/uv.lock",
    "*/packages.lock.json", "*/flake.lock",
]


def is_excluded(path: str, extra_patterns=()) -> bool:
    p = "/" + path.replace("\\", "/").lower()
    return any(fnmatch.fnmatchcase(p, pat.lower()) for pat in (*EXCLUDE_PATTERNS, *extra_patterns))


def detect_language(path: str):
    name = posixpath.basename(path.replace("\\", "/")).lower()
    if name in FILENAMES:
        return FILENAMES[name]
    if name.startswith("dockerfile."):
        return "Dockerfile"
    _, ext = posixpath.splitext(name)
    return EXTENSIONS.get(ext)


def color_of(language: str) -> str:
    return LANGUAGES.get(language, ("#8b8b8b", "code"))[0]


def kind_of(language: str) -> str:
    return LANGUAGES.get(language, ("", "code"))[1]
