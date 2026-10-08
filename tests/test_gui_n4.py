"""Plan N4 through the GUI backend: the settings fields for cloud refinement."""
import json

from tests.test_gui_server import home, save_config, serve  # noqa: F401 (fixtures)


def test_settings_save_the_refinement_choice(home, serve, tmp_path):
    save_config(home)
    app = serve()
    app.login()
    boot = app.client.get("/api/bootstrap").json()
    assert boot["config"]["refine_backend"] == "local"
    assert boot["config"]["refine_api_model"] == "whisper-large-v3"
    path = tmp_path / "config" / "lecture-cli" / "config.json"
    response = app.client.put("/api/config", json={"refine": True, "refine_backend": "api",
                                                    "refine_api_model": "whisper-large-v3"}, headers=app.origin)
    assert response.status_code == 200 and response.json()["problems"] == []
    saved = json.loads(path.read_text())
    assert (saved["refine"], saved["refine_backend"], saved["refine_api_model"]) == (True, "api", "whisper-large-v3")
    before = path.read_bytes()
    for change, field in (({"refine_backend": "cloud"}, "refine_backend"),
                          ({"refine_backend": "api", "refine_api_model": "  "}, "refine_api_model")):
        response = app.client.put("/api/config", json=change, headers=app.origin)
        assert response.status_code == 422 and response.json()["error"]["field"] == field
        assert path.read_bytes() == before
    # 不校正 keeps the chosen backend for the next time refinement is turned on.
    response = app.client.put("/api/config", json={"refine": False, "refine_backend": "api"}, headers=app.origin)
    assert response.status_code == 200
    assert json.loads(path.read_text())["refine_backend"] == "api"
