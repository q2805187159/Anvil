from __future__ import annotations

import base64
import re
import shlex
import unicodedata
from dataclasses import dataclass, field


SENSITIVE_PATH_PATTERNS = (
    re.compile(r"(^|/)\.ssh(/|$)"),
    re.compile(r"^/etc/"),
)

NETWORK_PATTERNS = (
    re.compile(r"\bcurl\b"),
    re.compile(r"\bwget\b"),
    re.compile(r"https?://([^/\s]+)"),
)

ANSI_ESCAPE_PATTERN = re.compile(
    r"\x1b"
    r"(?:"
    r"\[[\x30-\x3f]*[\x20-\x2f]*[\x40-\x7e]"
    r"|\][\s\S]*?(?:\x07|\x1b\\)"
    r"|[PX^_][\s\S]*?(?:\x1b\\)"
    r"|[\x20-\x2f]+[\x30-\x7e]"
    r"|[\x30-\x7e]"
    r")"
    r"|\x9b[\x30-\x3f]*[\x20-\x2f]*[\x40-\x7e]"
    r"|\x9d[\s\S]*?(?:\x07|\x9c)"
    r"|[\x80-\x9f]",
    re.DOTALL,
)
HAS_ANSI_ESCAPE_PATTERN = re.compile(r"[\x1b\x80-\x9f]")

WINDOWS_PATH_PATTERN = re.compile(r"(?i)(^|[\s\"'=:])([A-Z]:[\\/])")

SYSTEM_DESTRUCTIVE_PATHS = {
    "/",
    "/*",
    "/bin",
    "/boot",
    "/dev",
    "/etc",
    "/lib",
    "/lib64",
    "/proc",
    "/root",
    "/sbin",
    "/sys",
    "/usr",
    "/var",
}

SHELL_COMMAND_NAMES = {"bash", "sh", "zsh", "ksh"}
POWERSHELL_COMMAND_NAMES = {"powershell", "powershell.exe", "pwsh", "pwsh.exe"}

HIGH_IMPACT_COMMAND_NAMES = {
    "chmod",
    "chown",
    "del",
    "docker",
    "erase",
    "find",
    "xargs",
    "podman",
    "rd",
    "remove-item",
    "rm",
    "rmdir",
}

HARD_GUARDRAIL_FINDINGS = {
    "destructive_system_command",
    "remote_script_to_shell",
    "sensitive_shell_write",
}


@dataclass
class CommandSafetyReport:
    safe: bool
    findings: list[str] = field(default_factory=list)
    target_paths: list[str] = field(default_factory=list)
    target_hosts: list[str] = field(default_factory=list)


class CommandSafetyAnalyzer:
    def analyze(self, command: str, *, _depth: int = 0) -> CommandSafetyReport:
        findings: list[str] = []
        target_paths: list[str] = []
        target_hosts: list[str] = []
        command = _normalize_command_for_safety(command)
        tokens, parse_failed = _split_command_for_safety(command)
        if parse_failed:
            findings.append("shell_parse_failed")

        for token in tokens:
            if any(marker in token for marker in ("$(", "`", "&&", "||", "|")):
                findings.append("shell_control_operator")
            if token.startswith((">", ">>")):
                findings.append("shell_redirection")
            if "=" in token and not token.startswith(("/", "./", "../")) and token.split("=", 1)[0].isidentifier():
                findings.append("environment_injection")
            if _is_path_token(token):
                target_paths.append(token)
                if any(pattern.search(token) for pattern in SENSITIVE_PATH_PATTERNS):
                    findings.append("sensitive_path")
            for pattern in NETWORK_PATTERNS:
                match = pattern.search(token)
                if match:
                    findings.append("network_egress")
                    host = match.group(1) if match.groups() else token
                    if host and host not in target_hosts:
                        target_hosts.append(host)

        if any(_command_name(token) in HIGH_IMPACT_COMMAND_NAMES for token in tokens):
            findings.append("high_impact_command")
        if _is_destructive_system_command(tokens):
            findings.append("destructive_system_command")
        if _has_remote_script_to_shell(tokens):
            findings.append("remote_script_to_shell")
        if _has_sensitive_shell_write(tokens):
            findings.append("sensitive_shell_write")
        if _depth < 1:
            for nested_command in _nested_shell_commands(tokens):
                nested = self.analyze(nested_command, _depth=_depth + 1)
                findings.extend(finding for finding in nested.findings if finding in HARD_GUARDRAIL_FINDINGS)
                target_paths.extend(nested.target_paths)
                target_hosts.extend(host for host in nested.target_hosts if host not in target_hosts)
            for nested_command in _encoded_powershell_commands(tokens):
                nested = self.analyze(nested_command, _depth=_depth + 1)
                findings.extend(finding for finding in nested.findings if finding in HARD_GUARDRAIL_FINDINGS)
                target_paths.extend(nested.target_paths)
                target_hosts.extend(host for host in nested.target_hosts if host not in target_hosts)
            for nested_command in _powershell_command_strings(tokens):
                nested = self.analyze(nested_command, _depth=_depth + 1)
                findings.extend(finding for finding in nested.findings if finding in HARD_GUARDRAIL_FINDINGS)
                target_paths.extend(nested.target_paths)
                target_hosts.extend(host for host in nested.target_hosts if host not in target_hosts)

        return CommandSafetyReport(
            safe=not findings,
            findings=list(dict.fromkeys(findings)),
            target_paths=list(dict.fromkeys(target_paths)),
            target_hosts=target_hosts,
        )


def _is_destructive_system_command(tokens: list[str]) -> bool:
    for index, token in enumerate(tokens):
        command_name = _command_name(token)
        remainder = tokens[index + 1 :]
        if command_name == "rm":
            recursive = any(part == "--recursive" or (part.startswith("-") and "r" in part.lower()) for part in remainder)
            if not recursive:
                continue
            targets = [part for part in remainder if part != "--" and not part.startswith("-")]
            if any(_is_system_destructive_path(target) for target in targets):
                return True
        if command_name == "remove-item":
            recursive = any(part.lower() in {"-recurse", "-r"} for part in remainder)
            if not recursive:
                continue
            targets = [part for part in remainder if part != "--" and not part.startswith("-")]
            if any(_is_system_destructive_path(target) for target in targets):
                return True
        if command_name in {"rd", "rmdir"}:
            recursive = any(part.lower() in {"/s", "-s"} for part in remainder)
            if not recursive:
                continue
            targets = [part for part in remainder if part != "--" and not part.startswith(("-", "/"))]
            if any(_is_system_destructive_path(target) for target in targets):
                return True
        if command_name == "find":
            roots = _find_command_roots(remainder)
            if not roots or not any(_is_system_destructive_path(root) for root in roots):
                continue
            if "-delete" in remainder or _has_find_exec_rm(remainder) or _has_find_xargs_rm_pipeline(remainder):
                return True
    command_name = _primary_command_name(tokens)
    return command_name == "format" or command_name.startswith("mkfs")


def _normalize_command_for_safety(command: str) -> str:
    return unicodedata.normalize("NFKC", _strip_ansi_escape_sequences(command).replace("\x00", ""))


def _strip_ansi_escape_sequences(command: str) -> str:
    if not command or not HAS_ANSI_ESCAPE_PATTERN.search(command):
        return command
    return ANSI_ESCAPE_PATTERN.sub("", command)


def _split_command_for_safety(command: str) -> tuple[list[str], bool]:
    prefer_windows = bool(WINDOWS_PATH_PATTERN.search(command))
    modes = (False, True) if prefer_windows else (True, False)
    for posix in modes:
        try:
            tokens = shlex.split(command, posix=posix)
        except ValueError:
            continue
        return [_strip_outer_quotes(token) for token in tokens], False
    return [], True


def _strip_outer_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _find_command_roots(tokens: list[str]) -> list[str]:
    roots: list[str] = []
    for token in tokens:
        if token in {"--"}:
            continue
        if token.startswith(("-", "!", "(")):
            break
        roots.append(token)
    return roots


def _has_find_exec_rm(tokens: list[str]) -> bool:
    for index, token in enumerate(tokens):
        if token not in {"-exec", "-execdir"}:
            continue
        exec_tokens = tokens[index + 1 :]
        if not exec_tokens:
            continue
        if _command_name(exec_tokens[0]) == "rm":
            return True
        if _command_name(exec_tokens[0]) in SHELL_COMMAND_NAMES and _shell_invokes_rm(exec_tokens):
            return True
    return False


def _has_find_xargs_rm_pipeline(tokens: list[str]) -> bool:
    for index, token in enumerate(tokens):
        if token != "|":
            continue
        right = tokens[index + 1 :]
        if _primary_command_name(right) != "xargs":
            continue
        if _xargs_command_name(right[1:]) == "rm" or _xargs_invokes_shell_rm(right[1:]):
            return True
    return False


def _xargs_command_name(tokens: list[str]) -> str:
    command_tokens = _xargs_command_tokens(tokens)
    if not command_tokens:
        return ""
    return _command_name(command_tokens[0])


def _xargs_invokes_shell_rm(tokens: list[str]) -> bool:
    command_tokens = _xargs_command_tokens(tokens)
    if not command_tokens or _command_name(command_tokens[0]) not in SHELL_COMMAND_NAMES:
        return False
    return _shell_invokes_rm(command_tokens)


def _shell_invokes_rm(command_tokens: list[str]) -> bool:
    for index in range(1, len(command_tokens) - 1):
        option = command_tokens[index]
        if option == "--":
            break
        if not option.startswith("-"):
            break
        if option.startswith("--"):
            continue
        if "c" in option[1:]:
            try:
                nested_tokens = shlex.split(command_tokens[index + 1])
            except ValueError:
                return False
            return _primary_command_name(nested_tokens) == "rm"
    return False


def _xargs_command_tokens(tokens: list[str]) -> list[str]:
    options_with_values = {
        "-a",
        "--arg-file",
        "-d",
        "--delimiter",
        "-E",
        "--eof",
        "-I",
        "-i",
        "--replace",
        "-L",
        "--max-lines",
        "-l",
        "-n",
        "--max-args",
        "-P",
        "--max-procs",
        "-s",
        "--max-chars",
    }
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token == "--":
            index += 1
            break
        if not token.startswith("-"):
            break
        if token in options_with_values:
            index += 2
            continue
        index += 1
    if index >= len(tokens):
        return []
    return tokens[index:]


def _is_path_token(value: str) -> bool:
    if _is_windows_path(value):
        return True
    if value.startswith(("./", "../")) or value in {"/", "/*"}:
        return True
    return value.startswith("/") and not _is_windows_switch(value)


def _nested_shell_commands(tokens: list[str]) -> list[str]:
    commands: list[str] = []
    for shell_index, token in enumerate(tokens):
        if _command_name(token) not in SHELL_COMMAND_NAMES:
            continue
        for index in range(shell_index + 1, len(tokens) - 1):
            option = tokens[index]
            if option == "--":
                break
            if not option.startswith("-"):
                break
            if option.startswith("--"):
                continue
            if "c" in option[1:]:
                commands.append(tokens[index + 1])
                break
    return commands


def _encoded_powershell_commands(tokens: list[str]) -> list[str]:
    commands: list[str] = []
    for payload in _powershell_option_payloads(tokens, {"encodedcommand", "enc", "e"}, include_remainder=False):
        decoded = _decode_powershell_encoded_command(payload)
        if decoded:
            commands.append(decoded)
    return commands


def _powershell_command_strings(tokens: list[str]) -> list[str]:
    return _powershell_option_payloads(tokens, {"command", "c"}, include_remainder=True)


def _powershell_option_payloads(tokens: list[str], option_names: set[str], *, include_remainder: bool) -> list[str]:
    commands: list[str] = []
    for shell_index, token in enumerate(tokens):
        if _command_name(token) not in POWERSHELL_COMMAND_NAMES:
            continue
        for index in range(shell_index + 1, len(tokens)):
            raw_option = tokens[index]
            option = raw_option.lower()
            if option == "--":
                break
            if not option.startswith(("-", "/")):
                continue
            payload = _powershell_option_payload(tokens, index, option_names, include_remainder=include_remainder)
            if payload is not None:
                if payload:
                    commands.append(payload)
                break
    return commands


def _powershell_option_payload(
    tokens: list[str], index: int, option_names: set[str], *, include_remainder: bool
) -> str | None:
    raw_option = tokens[index]
    option = raw_option.lower()
    for option_name in option_names:
        for prefix in _powershell_option_prefixes(option_name):
            if option == prefix:
                if index + 1 >= len(tokens):
                    return None
                if include_remainder:
                    return " ".join(tokens[index + 1 :]).strip()
                return tokens[index + 1]
            for separator in (":", "="):
                inline_prefix = f"{prefix}{separator}"
                if not option.startswith(inline_prefix):
                    continue
                payload = raw_option[len(inline_prefix) :]
                if include_remainder:
                    return " ".join([payload, *tokens[index + 1 :]]).strip()
                if payload:
                    return payload
                if index + 1 < len(tokens):
                    return tokens[index + 1]
                return None
    return None


def _powershell_option_prefixes(option_name: str) -> tuple[str, ...]:
    return (f"-{option_name}", f"--{option_name}", f"/{option_name}")


def _decode_powershell_encoded_command(value: str) -> str | None:
    try:
        return base64.b64decode(_strip_outer_quotes(value), validate=True).decode("utf-16le")
    except (ValueError, UnicodeDecodeError):
        return None


def _has_remote_script_to_shell(tokens: list[str]) -> bool:
    for index, token in enumerate(tokens):
        if token != "|":
            continue
        left = tokens[:index]
        right = tokens[index + 1 :]
        if any(_command_name(part) in {"curl", "wget"} for part in left) and _primary_command_name(
            right
        ) in SHELL_COMMAND_NAMES:
            return True
    if _primary_command_name(tokens) in SHELL_COMMAND_NAMES and _has_remote_process_substitution(tokens):
        return True
    if _primary_command_name(tokens) in SHELL_COMMAND_NAMES and _has_remote_command_substitution(tokens):
        return True
    return False


def _has_remote_process_substitution(tokens: list[str]) -> bool:
    for index, token in enumerate(tokens):
        normalized = token.lower()
        if normalized in {"<(curl", "<(wget"}:
            return True
        if normalized == "<" and index + 1 < len(tokens) and tokens[index + 1].lower() in {"<(curl", "<(wget"}:
            return True
    return False


def _has_remote_command_substitution(tokens: list[str]) -> bool:
    for nested_command in _nested_shell_commands(tokens):
        if re.search(r"\$\(\s*(curl|wget)\b|`\s*(curl|wget)\b", nested_command, flags=re.IGNORECASE):
            return True
    return False


def _has_sensitive_shell_write(tokens: list[str]) -> bool:
    for index, token in enumerate(tokens):
        if token in {">", ">>"}:
            if any(_is_sensitive_path(part) for part in tokens[index + 1 : index + 2]):
                return True
            continue
        if token.startswith((">", ">>")) and len(token) > 1:
            if _is_sensitive_path(token.lstrip(">")):
                return True
            continue
        if _command_name(token) == "tee":
            if any(_is_sensitive_path(part) for part in tokens[index + 1 :] if not part.startswith("-")):
                return True
        if _command_name(token) in {"set-content", "add-content", "out-file"}:
            if _has_sensitive_powershell_write_target(tokens[index + 1 :]):
                return True
    return False


def _has_sensitive_powershell_write_target(tokens: list[str]) -> bool:
    path_options = {
        "-filepath",
        "-literalpath",
        "-path",
    }
    for index, token in enumerate(tokens):
        lower = token.lower()
        if lower in path_options and index + 1 < len(tokens):
            if _is_sensitive_path(tokens[index + 1]):
                return True
            continue
        for option in path_options:
            for separator in (":", "="):
                prefix = f"{option}{separator}"
                if lower.startswith(prefix):
                    payload = token[len(prefix) :]
                    if payload and _is_sensitive_path(payload):
                        return True
                    if not payload and index + 1 < len(tokens) and _is_sensitive_path(tokens[index + 1]):
                        return True
    positional_targets = [part for part in tokens if not part.startswith("-")]
    return bool(positional_targets and _is_sensitive_path(positional_targets[0]))


def _primary_command_name(tokens: list[str]) -> str:
    skip_wrapper_options = False
    for token in tokens:
        command_name = _command_name(token)
        if "=" in token and not token.startswith(("/", "./", "../")) and token.split("=", 1)[0].isidentifier():
            continue
        if command_name in {"sudo", "env", "command", "nohup"}:
            skip_wrapper_options = True
            continue
        if skip_wrapper_options and token.startswith("-"):
            continue
        return command_name
    return ""


def _command_name(token: str) -> str:
    return token.rsplit("/", 1)[-1].rsplit("\\", 1)[-1].lower()


def _is_sensitive_path(value: str) -> bool:
    normalized = _strip_outer_quotes(value).replace("\\", "/").lower()
    if any(pattern.search(normalized) for pattern in SENSITIVE_PATH_PATTERNS):
        return True
    if _is_env_secret_path(normalized):
        return True
    return (
        normalized.endswith("/windows/system32/drivers/etc/hosts")
        or "/windows/system32/drivers/etc/" in normalized
    )


def _is_env_secret_path(value: str) -> bool:
    filename = value.rsplit("/", 1)[-1]
    if filename == ".env":
        return True
    if not filename.startswith(".env."):
        return False
    return filename not in {".env.example", ".env.sample", ".env.template"}


def _is_windows_switch(value: str) -> bool:
    return len(value) == 2 and value.startswith("/") and value[1].isalpha()


def _is_windows_path(value: str) -> bool:
    return bool(re.match(r"(?i)^[A-Z]:[\\/]", _strip_outer_quotes(value)))


def _is_system_destructive_path(value: str) -> bool:
    value = _strip_outer_quotes(value)
    if _is_windows_system_destructive_path(value):
        return True
    if value in {"/", "/*"}:
        return True
    normalized = value.replace("\\", "/").rstrip("/")
    if normalized in {"", "*"}:
        return False
    if normalized in SYSTEM_DESTRUCTIVE_PATHS:
        return True
    return any(normalized.startswith(f"{path}/") for path in SYSTEM_DESTRUCTIVE_PATHS if path not in {"/", "/*"})


def _is_windows_system_destructive_path(value: str) -> bool:
    normalized = value.replace("\\", "/").rstrip("/*").rstrip("/")
    if re.fullmatch(r"(?i)[a-z]:", normalized):
        return True
    match = re.match(r"(?i)^([a-z]:)(/.*)$", normalized)
    if not match:
        return False
    suffix = match.group(2).lower()
    system_roots = (
        "/program files",
        "/program files (x86)",
        "/programdata",
        "/users",
        "/windows",
    )
    return any(suffix == root or suffix.startswith(f"{root}/") for root in system_roots)
