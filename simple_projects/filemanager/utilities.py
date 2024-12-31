from pathlib import Path


def generate_directory_structure(path: Path, indent: str = "") -> str:
    structure = ""
    for item in path.iterdir():
        if item.is_dir():
            structure += f"{indent}📁 {item.name}/\n"
            structure += generate_directory_structure(item, indent + "    ")
        else:
            structure += f"{indent}📄 {item.name}\n"
    return structure

