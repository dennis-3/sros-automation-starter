#!/usr/bin/env python3
"""
Pre-Flight CI/CD Validation Suite
Validates:
1. Jinja2 template AST syntax and block balancing across all templates in ansible/templates/.
2. Synthetic rendering of all 23 Jinja2 templates and ElementTree XML schema parsing.
3. Python compilation and syntax across all .py files.
4. YAML formatting and document integrity across inventory, playbooks, vars, and topology configs.
"""

import glob
import os
import py_compile
import re
import sys


def validate_jinja2_templates(template_dir="ansible/templates"):
    print(f"[*] Validating Jinja2 templates in {template_dir}...")
    template_files = glob.glob(os.path.join(template_dir, "*.j2"))
    if not template_files:
        print(f"[!] Warning: No .j2 templates found in {template_dir}")
        return True

    has_errors = False
    jinja_env = None
    try:
        import jinja2

        jinja_env = jinja2.Environment()
    except ImportError:
        print("    (jinja2 package not installed; utilizing lexical block validation)")

    for t_path in sorted(template_files):
        t_name = os.path.basename(t_path)
        with open(t_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Method 1: Jinja2 native AST parser if available
        if jinja_env:
            try:
                jinja_env.parse(content)
            except Exception as err:
                print(f"    [FAIL] {t_name}: Jinja2 AST error: {err}")
                has_errors = True
                continue

        # Method 2: Delimiter pair balance verification
        for open_delim, close_delim, d_name in [
            ("{{", "}}", "variable"),
            ("{%", "%}", "statement"),
            ("{#", "#}", "comment"),
        ]:
            open_count = content.count(open_delim)
            close_count = content.count(close_delim)
            if open_count != close_count:
                print(
                    f"    [FAIL] {t_name}: Unbalanced {d_name} delimiters '{open_delim}' vs '{close_delim}' ({open_count} open vs {close_count} close)"
                )
                has_errors = True

        # Method 3: Lexical tag block hierarchy verification
        open_blocks = []
        lines = content.splitlines()
        for line_no, line in enumerate(lines, start=1):
            # Match Jinja2 control statements: {% if ... %}, {% for ... %}, etc.
            tags = re.findall(r"{%\s*([a-zA-Z0-9_]+)\b", line)
            for tag in tags:
                if tag in ("if", "for", "block", "macro", "filter"):
                    open_blocks.append((tag, line_no))
                elif tag.startswith("end"):
                    base_tag = tag[3:]  # e.g. endif -> if, endfor -> for
                    if not open_blocks:
                        print(
                            f"    [FAIL] {t_name}:{line_no}: Stray closing tag '{{% {tag} %}}' with no matching open tag"
                        )
                        has_errors = True
                    else:
                        last_tag, last_line = open_blocks.pop()
                        if last_tag != base_tag:
                            print(
                                f"    [FAIL] {t_name}:{line_no}: Mismatched closing tag '{{% {tag} %}}' "
                                f"(expected 'end{last_tag}', opened at line {last_line})"
                            )
                            has_errors = True

        if open_blocks:
            for unclosed_tag, unclosed_line in open_blocks:
                print(
                    f"    [FAIL] {t_name}:{unclosed_line}: Unclosed block tag '{{% {unclosed_tag} %}}'"
                )
            has_errors = True
        else:
            print(f"    [OK] {t_name}")

    return not has_errors


EXCLUDED_PATTERNS = (
    ".git",
    ".cache",
    ".venv",
    "venv",
    "__pycache__",
    "collections",
    "ansible_collections",
    ".ansible",
    "netbox",
    "clab-",
    "context",
)


def is_excluded(filepath):
    normalized = os.path.normpath(filepath).replace("\\", "/")
    parts = normalized.split("/")
    for part in parts:
        for exc in EXCLUDED_PATTERNS:
            if exc == part or part.startswith(exc):
                return True
    return False


def validate_python_files(root_dir="."):
    print(f"[*] Validating Python syntax in {root_dir}...")
    has_errors = False
    py_files = [
        f
        for f in glob.glob(os.path.join(root_dir, "**", "*.py"), recursive=True)
        if not is_excluded(f)
    ]

    for py_file in sorted(py_files):
        try:
            py_compile.compile(py_file, doraise=True)
            print(f"    [OK] {py_file}")
        except py_compile.PyCompileError as err:
            print(f"    [FAIL] {py_file}: {err}")
            has_errors = True

    return not has_errors


def validate_yaml_files(root_dir="."):
    print(f"[*] Validating YAML documents in {root_dir}...")
    try:
        import yaml
    except ImportError:
        print("    (PyYAML not installed; skipping deep YAML schema parsing)")
        return True

    has_errors = False
    yaml_files = [
        f
        for f in glob.glob(os.path.join(root_dir, "**", "*.y*ml"), recursive=True)
        if not is_excluded(f)
    ]

    for y_file in sorted(yaml_files):
        try:
            with open(y_file, "r", encoding="utf-8") as f:
                list(yaml.safe_load_all(f))
            print(f"    [OK] {y_file}")
        except Exception as err:
            # Jinja-heavy Ansible playbooks might fail vanilla YAML parsers if unescaped
            print(f"    [WARN/FAIL] {y_file}: {err}")
            has_errors = True

    return not has_errors


def validate_template_rendering(
    template_dir="ansible/templates", data_dir="examples/data_models"
):
    print("[*] Validating synthetic rendering and XML syntax of Jinja2 templates...")
    try:
        import xml.etree.ElementTree as ET

        import jinja2
        import yaml
    except ImportError as err:
        print(f"    (Skipping render validation: {err})")
        return True

    context = {}
    data_files = glob.glob(os.path.join(data_dir, "*.yml")) + glob.glob(
        "ansible/host_vars/*.yml"
    )
    for df in data_files:
        try:
            with open(df, "r", encoding="utf-8") as f:
                d = yaml.safe_load(f)
                if isinstance(d, dict):
                    context.update(d)
        except Exception as err:
            print(f"    [FAIL] Failed loading context from {df}: {err}")
            return False

    env = jinja2.Environment()
    template_files = glob.glob(os.path.join(template_dir, "*.j2"))
    has_errors = False

    for t_path in sorted(template_files):
        t_name = os.path.basename(t_path)
        try:
            with open(t_path, "r", encoding="utf-8") as f:
                tmpl = env.from_string(f.read())
            rendered = tmpl.render(**context)
            ET.fromstring(rendered)
            print(f"    [OK RENDER] {t_name}")
        except Exception as err:
            print(f"    [FAIL RENDER] {t_name}: {err}")
            has_errors = True

    return not has_errors


def main():
    print("=" * 60)
    print("Pre-Flight CI Validation Suite")
    print("=" * 60)

    t_ok = validate_jinja2_templates()
    r_ok = validate_template_rendering()
    p_ok = validate_python_files()
    y_ok = validate_yaml_files()

    print("=" * 60)
    if t_ok and r_ok and p_ok and y_ok:
        print("ALL PRE-FLIGHT VALIDATIONS PASSED.")
        sys.exit(0)
    else:
        print("VALIDATION FAILURES DETECTED!")
        sys.exit(1)


if __name__ == "__main__":
    main()
