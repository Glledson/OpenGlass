/**
 * Card de community: render, filtro, ordenação e escape.
 *
 * Roda contra o `index.html` de verdade (ver helpers.js) e o payload gerado do
 * equipamento real: 32 prefixos, 68 rotas, 12 ASs de origem, 23 best path.
 *
 *     cd tests/js && npm install && npm test
 */

const test = require("node:test");
const assert = require("node:assert/strict");

const { abrirPagina, payloadHostil } = require("./helpers");
const PAYLOAD = require("./fixtures/community_no-export.json");

const { total_prefixes: PREFIXOS, best_paths: BEST, origin_count: ASS } = PAYLOAD;

function comPagina(fn) {
  const pagina = abrirPagina();
  try {
    return fn(pagina);
  } finally {
    pagina.fechar();
  }
}

test("renderiza uma linha por prefixo, com as quatro colunas", () => {
  comPagina((p) => {
    assert.equal(p.linhas(), PREFIXOS);
    assert.equal(p.coluna("origin_as").textContent, "AS de origem ↑", "nasce ordenada por AS");
    assert.equal(p.coluna("prefix").textContent, "Prefixo");
    assert.equal(p.coluna("next_hop").textContent, "Next hop");
    assert.equal(p.coluna("best").textContent, "Best");
  });
});

test("toda linha tem quatro células e prefixo preenchido", () => {
  comPagina((p) => {
    const linhas = [...p.doc.querySelectorAll("tbody tr")];
    const invalidas = linhas.filter(
      (tr) => tr.children.length !== 4 || !tr.children[1].textContent.trim(),
    );
    assert.equal(invalidas.length, 0, "toda linha precisa de 4 células e de prefixo");
  });
});

test("a badge de best aparece nas linhas marcadas", () => {
  comPagina((p) => {
    const badges = p.doc.querySelectorAll("tbody .badge.success");
    assert.equal(badges.length, BEST, "o número de badges tem de bater com best_paths");
  });
});

test("busca por next hop filtra e o contador acompanha", () => {
  comPagina((p) => {
    const alvo = PAYLOAD.origins[0].next_hops[0];
    p.digitar(alvo);
    const esperado = PAYLOAD.origins
      .flatMap((o) => o.entries)
      .filter((e) => e.next_hop === alvo).length;
    assert.ok(esperado > 0, "o next hop da fixture precisa existir");
    assert.equal(p.linhas(), esperado);
    assert.equal(p.contador().textContent, `${esperado} de ${PREFIXOS} prefixos`);
  });
});

test("busca por AS de origem filtra", () => {
  comPagina((p) => {
    const asn = PAYLOAD.origins[0].origin_as;
    p.digitar(asn);
    assert.equal(p.linhas(), PAYLOAD.origins[0].entries.length);
  });
});

test("busca casa em prefixo, next hop ou AS, sem diferenciar maiúsculas", () => {
  comPagina((p) => {
    const todas = PAYLOAD.origins.flatMap((o) => o.entries);
    const casas = (termo) =>
      todas.filter(
        (e) =>
          e.prefix.toLowerCase().includes(termo) ||
          e.next_hop.toLowerCase().includes(termo) ||
          String(e.origin_as).toLowerCase().includes(termo),
      ).length;

    p.digitar("45.6.136.0");
    assert.equal(p.linhas(), casas("45.6.136.0"));
    // o mesmo termo em caixa alta tem de achar o mesmo número de linhas
    p.digitar("45.6.136.0".toUpperCase());
    assert.equal(p.linhas(), casas("45.6.136.0"), "a busca não pode diferenciar caixa");

    p.digitar("nao-existe-zzz");
    assert.equal(p.linhas(), 0);
  });
});

test("o campo de busca não é recriado ao filtrar", () => {
  // Recriar o input a cada tecla destrói a digitação no meio: o usuário digita
  // um caractere e o campo some. Por isso o toolbar nasce uma vez e só o corpo
  // da tabela é trocado.
  comPagina((p) => {
    const antes = p.busca();
    antes.focus();
    p.digitar("187.16");
    assert.equal(p.busca(), antes, "o input precisa ser o mesmo elemento");
    assert.equal(p.doc.activeElement, antes, "o foco não pode pular para outro elemento");
  });
});

test("digitar caractere a caractere filtra junto com o texto", () => {
  comPagina((p) => {
    p.digitar("");
    const alvo = PAYLOAD.origins[0].next_hops[0];
    for (const ch of alvo) {
      const campo = p.busca();
      campo.value += ch;
      campo.dispatchEvent(new p.window.Event("input", { bubbles: true }));
    }
    assert.equal(p.busca().value, alvo);
    const esperado = PAYLOAD.origins.flatMap((o) => o.entries).filter((e) => e.next_hop === alvo).length;
    assert.equal(p.linhas(), esperado);
  });
});

test("seletor de AS restringe e volta ao listar todos", () => {
  comPagina((p) => {
    const select = p.seletorAS();
    const asn = PAYLOAD.origins[1].origin_as;
    select.value = asn;
    select.dispatchEvent(new p.window.Event("change", { bubbles: true }));
    assert.equal(p.linhas(), PAYLOAD.origins[1].entries.length);

    const todos = p.seletorAS();
    todos.value = "";
    todos.dispatchEvent(new p.window.Event("change", { bubbles: true }));
    assert.equal(p.linhas(), PREFIXOS);
  });
});

test("filtro só best path bate com best_paths", () => {
  comPagina((p) => {
    const caixa = p.caixaBest();
    caixa.checked = true;
    caixa.dispatchEvent(new p.window.Event("change", { bubbles: true }));
    assert.equal(p.linhas(), BEST);
    assert.equal(p.caixaBest().checked, true, "o checkbox não pode perder o estado");
  });
});

test("ordenar por coluna e inverter no segundo clique", () => {
  comPagina((p) => {
    const prefixos = PAYLOAD.origins.flatMap((o) => o.entries).map((e) => e.prefix);
    const col = (a, b) => a.localeCompare(b, "pt-BR", { numeric: true });

    p.clicarColuna("prefix");
    assert.equal(p.primeiraLinha(2), [...prefixos].sort(col)[0], "crescente começa no menor");
    assert.match(p.coluna("prefix").textContent, /↑/);

    p.clicarColuna("prefix");
    assert.equal(p.primeiraLinha(2), [...prefixos].sort((a, b) => -col(a, b))[0], "decrescente começa no maior");
    assert.match(p.coluna("prefix").textContent, /↓/);
  });
});

test("ordenar por best traz as best no topo", () => {
  comPagina((p) => {
    p.clicarColuna("best");
    const primeira = p.doc.querySelector("tbody tr");
    assert.match(primeira.textContent, /best/);
  });
});

test("ordenar por next hop respeita a coluna, não a linha", () => {
  comPagina((p) => {
    p.clicarColuna("next_hop");
    const hops = [...p.doc.querySelectorAll("tbody tr")].map((tr) => tr.children[2].textContent);
    const ordenados = [...hops].sort((a, b) => a.localeCompare(b, "pt-BR", { numeric: true }));
    assert.deepEqual(hops, ordenados);
  });
});

test("ordem e filtro sobrevivem a um novo filtro", () => {
  comPagina((p) => {
    p.clicarColuna("prefix");
    p.digitar("45.6");
    assert.match(p.coluna("prefix").textContent, /[↑↓]/, "a ordenação continua marcada");
    // a busca casa em prefixo, next hop OU AS: uma linha pode ter passado pelo
    // next hop, então o filtro não garante "45.6" no prefixo
    const linhas = [...p.doc.querySelectorAll("tbody tr")];
    const casa = (tr) =>
      ["45.6"].some((t) =>
        [tr.children[0].textContent, tr.children[1].textContent, tr.children[2].textContent].some(
          (celula) => celula.toLowerCase().includes(t),
        ),
      );
    linhas.forEach((tr) => assert.ok(casa(tr), `linha não casa com o filtro: ${tr.textContent}`));
    assert.ok(linhas.length > 0 && linhas.length < PREFIXOS);
  });
});

test("busca sem resultado mostra estado vazio e some com a tabela", () => {
  comPagina((p) => {
    p.digitar("nao-existe-zzz");
    assert.equal(p.linhas(), 0);
    assert.match(p.texto(), /Nenhuma rota casa/);
    assert.equal(p.contador().textContent, `0 de ${PREFIXOS} prefixos`);
  });
});

test("resultado novo não herda o filtro da consulta anterior", () => {
  comPagina((p) => {
    p.digitar("45.6.136.0");
    assert.ok(p.linhas() < PREFIXOS);
    p.render(PAYLOAD);
    assert.equal(p.linhas(), PREFIXOS, "o filtro anterior tem de ser descartado");
    assert.equal(p.busca().value, "");
    assert.equal(p.seletorAS().value, "");
    assert.equal(p.caixaBest().checked, false);
  });
});

test("payload sem prefixos não quebra", () => {
  comPagina((p) => {
    p.render({ type: "bgp_community" });
    assert.match(p.texto(), /Nenhum prefixo/);
    assert.equal(p.doc.querySelector("#community-search"), null);
  });
});

test("payload sem community mostra o rótulo genérico", () => {
  comPagina((p) => {
    p.render({ ...PAYLOAD, community: "" });
    assert.match(p.texto(), /busca por community/);
  });
});

test("dado do equipamento nunca vira HTML executável", () => {
  comPagina((p) => {
    // Os ícones da própria página são <svg>, então contar tag por tag não
    // serve: o que importa é o dado não acrescentar NENHUM elemento nem
    // atributo. A estrutura do DOM tem de ser idêntica com e sem o ataque.
    const antes = p.doc.getElementById("parsed").querySelectorAll("*").length;
    const handlersAntes = p.atributosDeEvento().length;

    p.render(payloadHostil(PAYLOAD));
    const raiz = p.doc.getElementById("parsed");

    assert.equal(raiz.querySelectorAll("*").length, antes, "o dado criou elementos no DOM");
    assert.equal(p.atributosDeEvento().length, handlersAntes, "o dado criou atributo on*");
    for (const tag of ["img", "script", "iframe", "object", "embed", "b"]) {
      assert.equal(raiz.querySelectorAll(tag).length, 0, `o dado criou <${tag}>`);
    }

    // o texto aparece, mas escapado
    const texto = raiz.textContent;
    assert.match(texto, /<img src=x onerror=alert\(1\)>/);
    assert.match(texto, /8\.8\.8\.0\/24<script>alert\(2\)<\/script>/);
  });
});

test("nenhum erro de JS durante a sessão", () => {
  comPagina((p) => {
    p.digitar("45");
    p.clicarColuna("best");
    p.render(PAYLOAD);
    assert.deepEqual(p.erros, []);
  });
});

test("o seletor lista os ASs de origem, incluindo o local", () => {
  comPagina((p) => {
    const opcoes = [...p.seletorAS().querySelectorAll("option")].map((o) => o.value);
    const esperados = PAYLOAD.origins.filter((o) => !o.is_local).map((o) => o.origin_as);
    assert.equal(ASS, PAYLOAD.origins.length);
    assert.deepEqual(opcoes.slice(1), esperados, "uma opção por AS que anuncia");
    assert.match(p.seletorAS().options[0].textContent, /todos os ASs/);
  });
});
