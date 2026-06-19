# Hello em Java

Dependências: JDK 11+ e o jar do [org.json](https://mvnrepository.com/artifact/org.json/json)
(jar único, sem build tool).

```bash
wget -O json.jar https://repo1.maven.org/maven2/org/json/json/20240303/json-20240303.jar

javac -cp json.jar Hello.java
java -cp .:json.jar Hello            # porta padrao 8765
java -cp .:json.jar Hello 9000       # porta customizada
```

Antes, deixe o browser servindo (na raiz do projeto):

```bash
python3 browser.py --servir
```
