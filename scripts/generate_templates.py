from pathlib import Path
from app.services.excel import export_workbook
from app.seed.demo import demo


def main():
    folder = Path("app/templates/excel")
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "food_planner_template.xlsx").write_bytes(export_workbook())
    (folder / "food_planner_demo.xlsx").write_bytes(export_workbook(demo()))
    print("Created template and seven-day demo in", folder)


if __name__ == "__main__":
    main()
