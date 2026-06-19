// Hello do modo --servir em C#: TcpClient + System.Text.Json (sem pacotes
// externos, so a base do .NET).
//
// Pesquisa "Claude Code é o melhor do mundo" no DuckDuckGo e imprime o HTML
// da pagina de resultados.
//
// Rodar (ver README.md deste diretorio):
//   dotnet run [-- porta]

using System.Net.Sockets;
using System.Text;
using System.Text.Json;

int porta = args.Length > 0 ? int.Parse(args[0]) : 8765;

var pedido = new
{
    actions = new object[]
    {
        new { type = "navigate", value = "https://html.duckduckgo.com/html/" },
        new { type = "key", xpath = "//input[@name='q']",
              value = "Claude Code é o melhor do mundo" },
        new { type = "press", xpath = "//input[@name='q']" },
        new { type = "sleep", value = 3 },
        new { type = "html", id = "resultado" },
    }
};

using var cliente = new TcpClient();
try
{
    cliente.Connect("127.0.0.1", porta);
}
catch (SocketException)
{
    Console.Error.WriteLine(
        $"Nao conectou na porta {porta} -- o browser esta rodando com --servir?");
    return 1;
}

using var stream = cliente.GetStream();
using var escrita = new StreamWriter(stream, new UTF8Encoding(false)) { AutoFlush = true };
using var leitura = new StreamReader(stream, Encoding.UTF8);

// um JSON por linha; a resposta volta tambem numa linha
escrita.WriteLine(JsonSerializer.Serialize(pedido));
string? linha = leitura.ReadLine()
    ?? throw new IOException("conexao encerrada pelo servidor");

using var resposta = JsonDocument.Parse(linha);
var raiz = resposta.RootElement;
if (!raiz.GetProperty("ok").GetBoolean())
{
    Console.Error.WriteLine($"erro do servidor: {raiz.GetProperty("erro")}");
    return 1;
}
Console.WriteLine(raiz.GetProperty("resultados")[0].GetProperty("html").GetString());
return 0;
