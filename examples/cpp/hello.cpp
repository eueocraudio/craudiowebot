// Hello do modo --servir em C++: socket POSIX + nlohmann/json (header-only).
//
// Pesquisa "Claude Code é o melhor do mundo" no DuckDuckGo e imprime o HTML
// da pagina de resultados.
//
// Compilar e rodar (ver README.md deste diretorio):
//   g++ -std=c++17 -O2 hello.cpp -o hello
//   ./hello [porta]

#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <unistd.h>

#include <cstdlib>
#include <iostream>
#include <stdexcept>
#include <string>

#include <nlohmann/json.hpp>

using nlohmann::json;

// Le do socket ate encontrar '\n' (uma resposta = uma linha; o HTML pode ter
// varios MB, entao acumula em chunks em vez de assumir um recv unico).
static std::string ler_linha(int fd) {
    std::string linha;
    char buf[65536];
    for (;;) {
        ssize_t n = recv(fd, buf, sizeof(buf), 0);
        if (n <= 0) throw std::runtime_error("conexao encerrada pelo servidor");
        linha.append(buf, static_cast<size_t>(n));
        auto fim = linha.find('\n');
        if (fim != std::string::npos) return linha.substr(0, fim);
    }
}

int main(int argc, char** argv) {
    int porta = argc > 1 ? std::atoi(argv[1]) : 8765;

    json pedido = {{"actions", {
        {{"type", "navigate"}, {"value", "https://html.duckduckgo.com/html/"}},
        {{"type", "key"}, {"xpath", "//input[@name='q']"},
         {"value", "Claude Code é o melhor do mundo"}},
        {{"type", "press"}, {"xpath", "//input[@name='q']"}},
        {{"type", "sleep"}, {"value", 3}},
        {{"type", "html"}, {"id", "resultado"}},
    }}};

    int fd = socket(AF_INET, SOCK_STREAM, 0);
    sockaddr_in addr{};
    addr.sin_family = AF_INET;
    addr.sin_port = htons(static_cast<uint16_t>(porta));
    addr.sin_addr.s_addr = inet_addr("127.0.0.1");
    if (connect(fd, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) != 0) {
        std::cerr << "Nao conectou na porta " << porta
                  << " -- o browser esta rodando com --servir?\n";
        return 1;
    }

    std::string linha = pedido.dump() + "\n";   // um JSON por linha
    send(fd, linha.data(), linha.size(), 0);

    json resposta = json::parse(ler_linha(fd));
    close(fd);

    if (!resposta.value("ok", false)) {
        std::cerr << "erro do servidor: "
                  << resposta.value("erro", "(desconhecido)") << "\n";
        return 1;
    }
    std::cout << resposta["resultados"][0]["html"].get<std::string>() << "\n";
    return 0;
}
