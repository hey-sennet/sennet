"""Reading Tailscale for the phone address: setup's question and doctor's checks."""
from agent_talk import cli, setup, tailscale
from agent_talk.config import Settings
from test_setup import LAPTOP, Scripted

UP = {"BackendState": "Running", "Self": {"DNSName": "desk.tail1234.ts.net."}}
SERVING = {"TCP": {"443": {"HTTPS": True}},
           "Web": {"desk.tail1234.ts.net:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:7710"}}}}}


def test_the_address_is_the_machine_name_over_https():
    assert tailscale.address(UP) == "https://desk.tail1234.ts.net"
    assert tailscale.address({**UP, "BackendState": "Stopped"}) is None
    assert tailscale.address({"BackendState": "Running", "Self": {}}) is None
    assert tailscale.address(None) is None


def test_serving_agent_talk_and_funnel():
    assert tailscale.serves(SERVING, 7710)
    assert not tailscale.serves(SERVING, 7799)
    assert tailscale.serves({"Web": {"x:443": {"Handlers": {"/": {"Proxy": "7710"}}}}}, 7710)
    assert not tailscale.serves({"Web": {"x:443": {"Handlers": {"/": {"Proxy": "http://10.0.0.5:7710"}}}}}, 7710)
    assert not tailscale.serves(None, 7710)
    assert not tailscale.funnel(SERVING)
    assert tailscale.funnel({**SERVING, "AllowFunnel": {"desk.tail1234.ts.net:443": True}})


def machine(tailnet=None, served=False):
    return setup.Machine(LAPTOP, False, tailnet, served)


def test_setup_offers_the_tailnet_address():
    settings, ui = Settings(), Scripted(True)
    setup._choose_address(ui, machine("https://desk.tail1234.ts.net"), settings)
    assert settings.server.public_url == "https://desk.tail1234.ts.net"
    assert "tailscale serve --bg 7710" in ui.output()


def test_setup_says_nothing_of_serve_when_it_already_serves():
    settings, ui = Settings(), Scripted(True)
    setup._choose_address(ui, machine("https://desk.tail1234.ts.net", served=True), settings)
    assert "tailscale serve" not in ui.output()


def test_declining_the_tailnet_address_clears_it():
    settings, ui = Settings(), Scripted(False)
    settings.server.public_url = "https://desk.tail1234.ts.net"
    setup._choose_address(ui, machine("https://desk.tail1234.ts.net"), settings)
    assert settings.server.public_url == ""


def test_setup_keeps_an_address_of_your_own():
    settings, ui = Settings(), Scripted()
    settings.server.public_url = "https://agent.example.com"
    setup._choose_address(ui, machine("https://desk.tail1234.ts.net"), settings)
    assert settings.server.public_url == "https://agent.example.com"
    assert not ui.asked


def test_without_tailscale_setup_explains_the_choices():
    settings, ui = Settings(), Scripted()
    setup._choose_address(ui, machine(), settings)
    assert settings.server.public_url == "" and not ui.asked
    assert "reverse proxy" in ui.output()


def test_doctor_reports_funnel_as_a_problem(paths, monkeypatch, capsys):
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/tailscale")
    monkeypatch.setattr(tailscale, "status", lambda: UP)
    monkeypatch.setattr(tailscale, "serve_status", lambda: {**SERVING, "AllowFunnel": {"desk.tail1234.ts.net:443": True}})
    assert not cli.access_ok(Settings(), paths)
    assert "Funnel is on" in capsys.readouterr().out


def test_doctor_says_how_to_serve(paths, monkeypatch, capsys):
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/tailscale")
    monkeypatch.setattr(tailscale, "status", lambda: UP)
    monkeypatch.setattr(tailscale, "serve_status", lambda: {})
    assert cli.access_ok(Settings(), paths)
    printed = capsys.readouterr().out
    assert "none paired yet" in printed and "tailscale serve --bg 7710" in printed
    assert "run `agent-talk setup` to use https://desk.tail1234.ts.net" in printed


def test_the_public_address_must_be_a_web_address(tmp_path):
    import pytest

    config = tmp_path / "config.toml"
    config.write_text('[server]\npublic_url = "desk.tail1234.ts.net"\n')
    with pytest.raises(ValueError, match="public_url"):
        Settings.load(config)
    config.write_text('[server]\npublic_url = "https://desk.tail1234.ts.net/"\n')
    assert Settings.load(config).server.public_url == "https://desk.tail1234.ts.net"
