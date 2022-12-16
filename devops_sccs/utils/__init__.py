def repo_slug_from_full_name(full_name: str) -> str:
    return full_name.split("/")[1]
