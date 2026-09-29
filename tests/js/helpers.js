/**
 * Monta a página de verdade para testar os visualizadores.
 *
 * Os testes não importam nada: carregam o `index.html` inteiro num jsdom e
 *Rodam o mesmo script que o navegador roda. É o que pega erro de runtime —
 * um `ReferenceError` numa função de render passa em qualquer checagem de
 * sintaxe e só aparece quando a card é desenhada de fato.
 */

const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");

const RAIZ = path.resolve(__dirname, "..", "..");
const INDEX = path.join(RAIZ, "src", "openglass", "static", "index.html");
const PAYLOAD = path.join(__dirname, "fixtures", "community_no-export.json");

/**
 * Respostas do carregamento inicial, com o formato que a API devolve de fato.
 * Sem isso o `renderSite` recebe lixo e a página estoura no load, poluindo a
 * saída do teste e escondendo erro de verdade.
 */
function fetchDaApi() {
  const rotas = {
    "/api/site": {
      org_name: "Teste",
      primary_asn: 64500,
      site_title: "OpenGlass (teste)",
      subtitle: "fixture de teste",
      title_mode: "subtitle",
      theme: "auto",
      links: [],
      menus: [],
    },
    "/api/devices": [],
  };
  return async (url) => {
    const rota = new URL(url, "http://localhost").pathname;
    const corpo = rota in rotas ? rotas[rota] : {};
    return { ok: true, json: async () => corpo };
  };
}

/**
 * Abre a página e devolve os Handles de teste.
 *
 * O stub de `fetch` entra em `beforeParse` de propósito: definido depois, o
 * script já rodou e o erro de load passa despercebido.
 *
 * `payload` aceita caminho de arquivo ou o objeto pronto. Objeto é o que os
 * testes de card do VRP usam: eles desenham payloads de IOS montados à mão
 * para provar que a card não inventa campo que aquele NOS não devolve, e não
 * faz sentido gravar um arquivo só para isso. `null` desenha a página sem
 * resultado nenhum.
 */
function abrirPagina({ payload = PAYLOAD } = {}) {
  const html = fs.readFileSync(INDEX, "utf8");
  const dom = new JSDOM(html, {
    runScripts: "dangerously",
    url: "http://localhost",
    beforeParse(w) {
      w.fetch = fetchDaApi();
      w.navigator.clipboard = { writeText: async () => {} };
    },
  });
  const { window } = dom;
  const doc = window.document;

  const erros = [];
  window.addEventListener("error", (e) => erros.push(String(e.error || e.message)));
  const erroOriginal = window.console.error;
  window.console.error = (...args) => {
    erros.push(args.map(String).join(" "));
    erroOriginal.apply(window.console, args);
  };

  const $parsed = doc.getElementById("parsed");
  const api = {
    window,
    doc,
    erros,
    /** Executa código no escopo do script da página (mesmo escopo do navegador). */
    run: (codigo) => window.eval(codigo),
    /** Desenha um resultado, como a interface faz ao receber a resposta. */
    render: (dado) => {
      $parsed.classList.remove("hidden");
      api.run(`renderParsed(${JSON.stringify(dado)});`);
    },
    busca: () => $parsed.querySelector("#community-search"),
    seletorAS: () => $parsed.querySelector("#community-as"),
    caixaBest: () => $parsed.querySelector("#community-best"),
    contador: () => $parsed.querySelector("#community-count"),
    coluna: (chave) =>
      [...$parsed.querySelectorAll("th.sortable")].find((th) => th.dataset.sort === chave),
    /** Elementos com atributo de evento (onclick, onerror...). */
    atributosDeEvento: () =>
      [...$parsed.querySelectorAll("*")].filter((el) =>
        [...el.attributes].some((a) => /^on/i.test(a.name)),
      ),
    /** Linhas do corpo, sem contar o cabeçalho. */
    linhas: () => $parsed.querySelectorAll("tbody tr").length,
    primeiraLinha: (coluna) =>
      $parsed.querySelector(`tbody tr td:nth-child(${coluna})`)?.textContent ?? "",
    texto: () => $parsed.textContent,
    digitar: (valor) => {
      const campo = api.busca();
      campo.value = valor;
      campo.dispatchEvent(new window.Event("input", { bubbles: true }));
    },
    clicarColuna: (chave) =>
      api.coluna(chave).dispatchEvent(new window.MouseEvent("click", { bubbles: true })),
    fechar: () => dom.window.close(),
  };

  if (payload) {
    const dado = typeof payload === "string" ? JSON.parse(fs.readFileSync(payload, "utf8")) : payload;
    api.render(dado);
  }
  return api;
}

/** Payload com string hostil, para checar que nada vira HTML executável. */
function payloadHostil(base) {
  const copia = JSON.parse(JSON.stringify(base));
  copia.origins[0].origin_as = '<img src=x onerror=alert(1)>';
  copia.origins[0].entries[0].prefix = "8.8.8.0/24<script>alert(2)</script>";
  copia.origins[0].next_hops = ['"><b>injetado'];
  copia.community = 'no-export"><script>alert(3)</script>';
  return copia;
}

module.exports = { abrirPagina, payloadHostil, INDEX, PAYLOAD };
