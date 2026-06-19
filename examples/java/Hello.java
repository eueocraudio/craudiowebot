// Hello do modo --servir em Java: java.net.Socket (JDK) + org.json (jar
// unico) para o JSON.
//
// Pesquisa "Claude Code é o melhor do mundo" no DuckDuckGo e imprime o HTML
// da pagina de resultados.
//
// Compilar e rodar (ver README.md deste diretorio):
//   javac -cp json.jar Hello.java
//   java -cp .:json.jar Hello [porta]

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.io.PrintWriter;
import java.net.Socket;
import java.nio.charset.StandardCharsets;

import org.json.JSONArray;
import org.json.JSONObject;

public class Hello {

    public static void main(String[] args) throws Exception {
        int porta = args.length > 0 ? Integer.parseInt(args[0]) : 8765;

        JSONObject pedido = new JSONObject().put("actions", new JSONArray()
            .put(new JSONObject().put("type", "navigate")
                .put("value", "https://html.duckduckgo.com/html/"))
            .put(new JSONObject().put("type", "key")
                .put("xpath", "//input[@name='q']")
                .put("value", "Claude Code é o melhor do mundo"))
            .put(new JSONObject().put("type", "press")
                .put("xpath", "//input[@name='q']"))
            .put(new JSONObject().put("type", "sleep").put("value", 3))
            .put(new JSONObject().put("type", "html").put("id", "resultado")));

        try (Socket s = new Socket("127.0.0.1", porta)) {
            PrintWriter escrita = new PrintWriter(
                s.getOutputStream(), true, StandardCharsets.UTF_8);
            BufferedReader leitura = new BufferedReader(
                new InputStreamReader(s.getInputStream(), StandardCharsets.UTF_8));

            escrita.println(pedido.toString());   // um JSON por linha
            String linha = leitura.readLine();    // uma resposta por linha
            if (linha == null) {
                throw new RuntimeException("conexao encerrada pelo servidor");
            }

            JSONObject resposta = new JSONObject(linha);
            if (!resposta.optBoolean("ok")) {
                System.err.println("erro do servidor: "
                    + resposta.optString("erro"));
                System.exit(1);
            }
            System.out.println(resposta.getJSONArray("resultados")
                .getJSONObject(0).getString("html"));
        }
    }
}
