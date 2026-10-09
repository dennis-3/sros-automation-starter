#!/usr/bin/env python3
"""
Comprehensive CI/CD Pre-Flight Validator
Validates:
1. Jinja2 template AST syntax and block balancing across all templates in ansible/templates/.
2. Python compilation and syntax across all .py files.
3. YAML formatting and document integrity across inventory, playbooks, vars, and topology configs.
"""
import os
import sys
import glob
import re
import py_compile

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

        # Method 2: Lexical tag balance verification
        open_blocks = []
        lines = content.splitlines()
        for line_no, line in enumerate(lines, start=1):
            # Match Jinja2 control statements: {% if ... %}, {% for ... %}, etc.
            tags = re.findall(r"{%\s*([a-zA-Z0-9_]+)\b", line)
            for tag in tags:
                if tag in ('if', 'for', 'block', 'macro', 'filter'):
                    open_blocks.append((tag, line_no))
                elif tag.startswith('end'):
                    base_tag = tag[3:] # e.g. endif -> if, endfor -> for
                    if not open_blocks:
                        print(f"    [FAIL] {t_name}:{line_no}: Stray closing tag '{{% {tag} %}}' with no matching open tag")
                        has_errors = True
                    else:
                        last_tag, last_line = open_blocks.pop()
                        if last_tag != base_tag:
                            print(f"    [FAIL] {t_name}:{line_no}: Mismatched closing tag '{{% {tag} %}}' (expected 'end{last_tag}', opened at line {last_line})")
                            has_errors = True

        if open_blocks:
            for unclosed_tag, unclosed_line in open_blocks:
                print(f"    [FAIL] {t_name}:{unclosed_line}: Unclosed block tag '{{% {unclosed_tag} %}}'")
            has_errors = True
        else:
            print(f"    [OK] {t_name}")

    return not has_errors

def validate_python_files(root_dir="."):
    print(f"[*] Validating Python syntax in {root_dir}...")
    has_errors = False
    py_files = [
        f for f in glob.glob(os.path.join(root_dir, "**", "*.py"), recursive=True)
        if not any(excluded in f for excluded in [".git", "__pycache__", ".venv", "venv", ".cache"])
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
        f for f in glob.glob(os.path.join(root_dir, "**", "*.y*ml"), recursive=True)
        if not any(excluded in f for excluded in [".git", ".cache", ".venv", "venv"])
    ]

    for y_file in sorted(yaml_files):
        try:
            with open(y_file, "r", encoding="utf-8") as f:
                yaml.safe_load(f)
            print(f"    [OK] {y_file}")
        except Exception as err:
            # Jinja-heavy Ansible playbooks might fail vanilla YAML parsers if unescaped
            print(f"    [WARN/FAIL] {y_file}: {err}")
            has_errors = True

    return not has_errors

def main():
    print("=" * 60)
    print("Pre-Flight CI Validation Suite")
    print("=" * 60)
    
    t_ok = validate_jinja2_templates()
    p_ok = validate_python_files()
    y_ok = validate_yaml_files()

    print("=" * 60)
    if t_ok and p_ok and y_ok:
        print("ALL PRE-FLIGHT VALIDATIONS PASSED.")
        sys.exit(0)
    else:
        print("VALIDATION FAILURES DETECTED!")
        sys.exit(1)

if __name__ == "__main__":
    main()
