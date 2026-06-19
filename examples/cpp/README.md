# Hello em C++

Dependência: [nlohmann/json](https://github.com/nlohmann/json) (header-only).

```bash
sudo apt install nlohmann-json3-dev      # Debian/Ubuntu
# Fedora: sudo dnf install json-devel

g++ -std=c++17 -O2 hello.cpp -o hello
./hello            # porta padrao 8765
./hello 9000       # porta customizada
```

Antes, deixe o browser servindo (na raiz do projeto):

```bash
python3 browser.py --servir
```
