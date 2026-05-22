def handle_create_widget(payload):
    if "name" not in payload:
        return 400, {"error": "name required"}
    return 201, {"id": 1, "name": payload["name"]}
