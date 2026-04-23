<<<<<<< HEAD
import os
import re

ENV_KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def parse_env_line(line, line_number):
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None

    if "=" not in stripped:
        raise ValueError(f"Invalid .env format on line {line_number}: missing '='")

    key, raw_value = stripped.split("=", 1)
    key = key.strip()
    if not ENV_KEY_PATTERN.match(key):
        raise ValueError(f"Invalid environment variable name on line {line_number}: {key}")

    value = raw_value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        value = value[1:-1]

    return key, value


def load_env_file(env_path, override=False):
    if not os.path.isfile(env_path):
        return {}

    loaded = {}
    with open(env_path, "r", encoding="utf-8") as env_file:
        for line_number, line in enumerate(env_file, start=1):
            parsed = parse_env_line(line, line_number)
            if not parsed:
                continue

            key, value = parsed
            if override or key not in os.environ:
                os.environ[key] = value
            loaded[key] = os.environ.get(key, value)

    return loaded


def get_env(name, default=None, required=False):
    value = os.environ.get(name, default)
    if isinstance(value, str):
        value = value.strip()

    if required and (value is None or value == ""):
        raise RuntimeError(f"Missing required environment variable: {name}")

    return value
=======
import os
import re

ENV_KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def parse_env_line(line, line_number):
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None

    if "=" not in stripped:
        raise ValueError(f"Invalid .env format on line {line_number}: missing '='")

    key, raw_value = stripped.split("=", 1)
    key = key.strip()
    if not ENV_KEY_PATTERN.match(key):
        raise ValueError(f"Invalid environment variable name on line {line_number}: {key}")

    value = raw_value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        value = value[1:-1]

    return key, value


def load_env_file(env_path, override=False):
    if not os.path.isfile(env_path):
        return {}

    loaded = {}
    with open(env_path, "r", encoding="utf-8") as env_file:
        for line_number, line in enumerate(env_file, start=1):
            parsed = parse_env_line(line, line_number)
            if not parsed:
                continue

            key, value = parsed
            if override or key not in os.environ:
                os.environ[key] = value
            loaded[key] = os.environ.get(key, value)

    return loaded


def get_env(name, default=None, required=False):
    value = os.environ.get(name, default)
    if isinstance(value, str):
        value = value.strip()

    if required and (value is None or value == ""):
        raise RuntimeError(f"Missing required environment variable: {name}")

    return value
>>>>>>> ccb977842caf5f5132d343a9efad487453325a7d
