from rich.table import Table


def dynamic_table(data: list[dict[str, str]], title: str) -> Table:
    # Dynamically add columns based on the keys of the JSON objects
    table = Table(title=title)
    if data:
        for key in data[0].keys():
            table.add_column(key.capitalize())

        # Add rows to the table
        for item in data:
            table.add_row(*[str(value) for value in item.values()])
    return table
