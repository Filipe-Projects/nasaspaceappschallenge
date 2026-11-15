# Guia da equipa

Guia rápido para correr, publicar e apresentar o BeyondtheBlue no
NASA Space Apps Challenge 2026 (desafio “Planet X and SPHEREx”).

## 1. Correr no computador

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt      # Linux/macOS: .venv/bin/pip
.venv\Scripts\python -m uvicorn spherex_viewer.main:app --port 8000
```

Abrir `http://127.0.0.1:8000/?target=barnard`.

Requisitos: Python 3.11 a 3.13. O Python 3.15 ainda não tem todos os pacotes
científicos compilados.

A primeira vez que se abre um campo novo, o servidor lê centenas de pequenos
pedaços de ficheiros do IRSA. Numa ligação lenta isto leva um a três minutos; a
partir daí o campo fica em cache e abre em segundos.

## 2. Preparar a demonstração

Aquecer a cache dos cinco alvos do enunciado:

```bash
.venv\Scripts\python scripts\precache.py
```

Correr os testes (não precisam de rede):

```bash
.venv\Scripts\pip install pytest
.venv\Scripts\python -m pytest -q
```

## 3. Publicar (a ferramenta tem de ser pública)

Opção mais simples, gratuita: **Hugging Face Spaces**.

1. Criar conta em huggingface.co e um Space novo do tipo **Docker**.
2. Fazer `git push` deste repositório para o Space (o cabeçalho do `README.md`
   já tem a configuração de que o Space precisa).
3. Em Settings, Variables, definir `SPHEREX_WARMUP` como `1` para os cinco alvos
   ficarem prontos ao arrancar.
4. Esperar pela construção da imagem e abrir o endereço do Space.

Alternativa: **Render**, usando o ficheiro `render.yaml` (escolher uma região nos
Estados Unidos; ver a nota no próprio ficheiro sobre o plano gratuito).

Depois de publicar:

* Confirmar que o repositório no GitHub está público: o endereço já está em
  `<meta name="repo">` no `frontend/index.html` e aparece como “Source code” na
  caixa About.
* Testar os cinco alvos no endereço público antes de submeter.

## 4. Roteiro para a demonstração (30 segundos)

| Tempo | O que mostrar | O que dizer |
| --- | --- | --- |
| 0 a 5 s | Página inicial: colar `Right Ascension 08h55m10.831s, declination -7d14m42.53s` (o texto do enunciado) e carregar em Search. Abrir este campo uma vez antes da apresentação, para ficar em cache | Qualquer pessoa escreve umas coordenadas e vê um pequeno filme desse pedaço de céu |
| 5 a 10 s | O aviso “WISE 0855−0714 was here in 2010”, botão **Follow it** | As coordenadas do enunciado são de 2010; a ferramenta sabe que a anã castanha já andou 2,2′ e leva-nos até ela |
| 10 a 16 s | `/?target=barnard&mode=diff`: o par vermelho/azul | A Barnard's Star moveu-se 10″ num ano, e a ferramenta mediu-o sozinha |
| 16 a 21 s | `/?target=epsindi-b&band=RGB`: duas setas paralelas, o par a laranja | As 102 bandas numa só imagem a cores: os objetos frios saem vermelhos. O par de anãs castanhas e a sua estrela, a 7′, movem-se juntos |
| 21 a 26 s | `/?target=rwaur`, separador Spectrum; depois `/?target=rdra`, separador Light curve | Espetros feitos na hora: três épocas, três espetros. E o ciclo de 245 dias de uma Mira nas seis bandas |
| 26 a 30 s | Menu de guardar: **Copy embed code** | Vídeo, PNG, FITS, ligação e código para embutir noutra página |

Fechar com o endereço público.

### Se o IRSA estiver em baixo no dia

Acontece (aconteceu durante o desenvolvimento). Os cinco alvos do enunciado abrem
sempre, porque vão guardados com o código em `spherex_viewer/snapshots/`; a página
indica a data da cópia. O que deixa de funcionar é um campo novo, nunca aberto:
aparece a frase “The SPHEREx archive at IRSA is not answering right now” e o botão
Try again. Nesse caso, saltar o primeiro passo do roteiro e começar no Barnard.

Com o IRSA a responder, vale a pena correr uma vez
`.venv\Scripts\python scripts\snapshot.py challenge-wise0855` e fazer commit do
resultado: guarda também o campo das coordenadas do enunciado, e o primeiro passo
do roteiro passa a funcionar sempre.

## 5. Ligações úteis para partilhar

* Campo por coordenadas: `/?ra=133.758&dec=-7.242&fov=8`
* Uma banda e modo diferença: `/?target=barnard&band=D6&mode=diff`
* Imagem a cores a partir das bandas: `/?target=barnard&band=RGB`
* Exposições individuais: `/?target=barnard&seq=exposure&band=D6&fov=3`
* Para embutir noutra página: menu de guardar, **Copy embed code** (ou acrescentar
  `&embed=1` ao endereço e usar num `<iframe>`)

## 6. O que cada critério de avaliação encontra

| Critério | Onde está |
| --- | --- |
| Relevância | Coordenadas → pequeno vídeo; os cinco alvos do enunciado testados e documentados no README |
| Validade | Astrometria entre épocas a 0,01 a 0,02 píxel; movimentos medidos coincidem com o catálogo; 34 testes automáticos; continua a funcionar com o IRSA em baixo |
| Impacto | Sem instalação, ligação partilhável, lista automática do que mudou para quem não sabe o que procurar |
| Criatividade | Um pedido por ficheiro graças à previsão da posição no detetor; deteção automática de movimento e variabilidade; soma com deslocamento ao longo de um movimento próprio |
| Apresentação | Roteiro acima; capturas em `docs/` |

## 7. Limitações a dizer com franqueza

* Medições rápidas: aberturas fixas, sem ajuste de PSF.
* Movimentos abaixo de cerca de 0,5″/ano ainda não se detetam; a base temporal
  cresce seis meses a cada passagem do SPHEREx.
* A lista automática é conservadora e só cobre fontes suficientemente brilhantes.
* Ler as flags de píxel custa cerca de 0,4 MB por exposição, por isso é opcional.
