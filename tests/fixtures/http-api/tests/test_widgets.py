from src.server import handle_create_widget


def test_create_widget_requires_name():
    status, body = handle_create_widget({})
    assert status == 400


def test_create_widget_returns_201():
    status, body = handle_create_widget({"name": "thing"})
    assert status == 201
    assert body["name"] == "thing"
