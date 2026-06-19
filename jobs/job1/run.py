import os


class Job():
    def __init__(self, browser, json_):
        self.json_  = json_;
        self.browser = browser;
        pass;

    def pre_action(self, action_json):
        # credenciais vem de variaveis de ambiente (nunca versionadas)
        if action_json.get("id") and action_json.get("id") == "email":
            action_json["value"] = os.environ.get("GMAIL_EMAIL", "");
        if action_json.get("id") and action_json.get("id") == "password":
            action_json["value"] = os.environ.get("GMAIL_PASSWORD", "");
        pass;

    def pos_action(self, action_json):
        # se a acao tiver xpath, le o valor do input html apontado por ele
        xpath = action_json.get("xpath")
        if xpath:
            self.browser.ler_valor(xpath)

    def finish(self):
        pass;
