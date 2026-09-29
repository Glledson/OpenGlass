/**
 * Cards do Huawei VRP: `ping`, `traceroute` e `bgp_prefix`.
 *
 * Roda contra o `index.html` de verdade (ver helpers.js) e contra payloads
 * gerados dos parsers sobre saída real de um NetEngine 8000 (VRP 8.200). Os
 * JSONs são conferidos contra o parser em `tests/test_vrp_parsers.py`, então
 * se o formato mudar aqui o CI acusa em vez de a card virar tela quebrada.
 *
 * O foco é o que o VRP acrescenta ao contrato do IOS: TTL e tempo por sonda no
 * ping, next hop por sonda no tracert, e communities/interface/duração/motivo da
 * derrota no detalhe BGP. Cada teste tem o par IOS correspondente, para provar
 * que nenhum desses campos inventa linha na card do outro NOS.
 *
 *     cd tests/js && npm install && npm test
 */

const test = require("node:test");
const assert = require("node:assert/strict");

const { abrirPagina } = require("./helpers");

const PING = require("./fixtures/vrp_ping.json");
const PING_LOSS = require("./fixtures/vrp_ping_loss.json");
const TRACERT = require("./fixtures/vrp_traceroute.json");
const TRACERT_STAR = require("./fixtures/vrp_traceroute_star.json");
const BGP = require("./fixtures/vrp_bgp_prefix.json");
const BGP_AGGR = require("./fixtures/vrp_bgp_prefix_aggr.json");
const BGP_NAO_ENCONTRADO = require("./fixtures/vrp_bgp_prefix_notfound.json");

function comPagina(payload, fn) {
  // `payload: null` para desenhar a página sem nenhum resultado: o render
  // inicial é o de community e atrapalharia a contagem de elementos
  const pagina = abrirPagina({ payload });
  try {
    return fn(pagina);
  } finally {
    pagina.fechar();
  }
}

const $ = (p, sel) => p.doc.querySelector(sel);
const todos = (p, sel) => [...p.doc.querySelectorAll(sel)];

// --- ping -----------------------------------------------------------------

test("ping VRP mostra TTL e tempo de cada sonda no title do chip", () => {
  comPagina(PING, (p) => {
    const chips = todos(p, ".probes .probe");
    assert.equal(chips.length, PING.probes.length);
    chips.forEach((chip, i) => {
      const sonda = PING.probes[i];
      assert.equal(chip.getAttribute("title"), `Resposta · TTL ${sonda.ttl} · ${sonda.time_ms} ms`);
    });
  });
});

test("ping VRP marca 100% de sucesso e os cinco recebidos", () => {
  comPagina(PING, (p) => {
    assert.equal($(p, ".ping-head .badge").className.includes("success"), true);
    assert.match(p.texto(), /100% de sucesso/);
    assert.match($(p, ".ping-stats").textContent, /Recebidos5/);
  });
});

test("ping VRP com perda não inventa tempo nas sondas expiradas", () => {
  comPagina(PING_LOSS, (p) => {
    assert.equal($(p, ".ping-head .badge").className.includes("failed"), true);
    const chips = todos(p, ".probes .probe");
    assert.equal(chips.length, 5);
    // um timeout não mediu tempo: o title não pode ter "0 ms" nem número
    chips.forEach((chip) => {
      const titulo = chip.getAttribute("title");
      assert.equal(titulo, "Tempo esgotado");
      assert.doesNotMatch(titulo, /TTL|\d+ ms/);
    });
  });
});

test("ping VRP sem latência diz que não mediu, em vez de mostrar 0", () => {
  comPagina(PING_LOSS, (p) => {
    assert.match(p.texto(), /Sem resposta/);
    assert.equal($(p, ".rtt"), null);
  });
});

test("ping VRP deixa o tempo limite em branco: o VRP não imprime o default", () => {
  comPagina(PING, (p) => {
    assert.equal(PING.timeout_s, null);
    assert.match($(p, ".ping-stats").textContent, /Tempo limite\s*—/);
  });
});

test("ping do IOS não ganha TTL nem tempo que o IOS não mediu", () => {
  // payload no formato do IOS: sem ttl e sem time_ms por sonda
  comPagina(
    {
      type: "ping",
      nos: "cisco_ios",
      status: "success",
      target: "8.8.8.8",
      sent: 5,
      received: 5,
      loss_percent: 0,
      success_percent: 100,
      size_bytes: 100,
      timeout_s: 2,
      rtt_ms: { min: 1, avg: 2, max: 3 },
      probes: [
        { symbol: "!", result: "reply", label: "Resposta" },
        { symbol: "!", result: "reply", label: "Resposta" },
      ],
    },
    (p) => {
      const chips = todos(p, ".probes .probe");
      assert.equal(chips.length, 2);
      chips.forEach((chip) => assert.equal(chip.getAttribute("title"), "Resposta"));
      assert.match($(p, ".ping-stats").textContent, /Tempo limite2s/);
    },
  );
});

// --- traceroute -----------------------------------------------------------

test("tracert VRP conta os seis saltos e marca chegada ao destino", () => {
  comPagina(TRACERT, (p) => {
    assert.equal(todos(p, ".tr-hop").length, TRACERT.hops.length);
    assert.match($(p, ".tr-head .badge").textContent, /Chegou ao destino/);
    assert.match($(p, ".tr-dest").textContent, /8\.8\.8\.8/);
  });
});

test("tracert VRP mostra a contagem de next hops alternativos do salto", () => {
  comPagina(TRACERT, (p) => {
    const linhas = todos(p, ".tr-hop");
    // salto 4 e 5 do equipamento saíram por dois endereços na mesma linha
    const comAlt = linhas.filter((l) => l.querySelector(".tr-alt"));
    assert.equal(comAlt.length, 2);
    // a contagem bate com o payload, e o title traz os endereços guardados
    comAlt.forEach((l) => {
      const marca = l.querySelector(".tr-alt");
      const extras = [...marca.getAttribute("title").matchAll(/[\d.]+/g)].length;
      assert.equal(marca.textContent, `+${extras}`);
    });
  });
});

test("tracert VRP guarda no title todos os endereços do caminho alternativo", () => {
  comPagina(TRACERT, (p) => {
    const hop4 = TRACERT.hops[3];
    const marca = todos(p, ".tr-hop")[3].querySelector(".tr-alt");
    assert.equal(marca.textContent, `+${hop4.addresses.length - 1}`);
    hop4.addresses.slice(1).forEach((ip) => {
      assert.ok(marca.getAttribute("title").includes(ip), `faltou ${ip} no title`);
    });
  });
});

test("tracert VRP não decora AS: o IOS é que mostra [AS N]", () => {
  comPagina(TRACERT, (p) => {
    assert.equal(todos(p, ".tr-asn").length, 0);
    assert.ok(TRACERT.hops.every((h) => h.asn === null));
  });
});

test("tracert VRP com estrela marca como incompleto e mostra — no salto mudo", () => {
  comPagina(TRACERT_STAR, (p) => {
    assert.equal(todos(p, ".tr-hop").length, 30);
    assert.match($(p, ".tr-head .badge").textContent, /Incompleto/);
    const mudos = todos(p, ".tr-hop").filter((l) => l.querySelector(".tr-ip").textContent.includes("—"));
    assert.equal(mudos.length, 30 - TRACERT_STAR.hops.filter((h) => h.ip).length);
    // e nenhum deles ganha next hop alternativo
    assert.equal(mudos.filter((l) => l.querySelector(".tr-alt")).length, 0);
  });
});

test("tracert VRP não escreve 0 ms no salto que expirou", () => {
  comPagina(TRACERT_STAR, (p) => {
    const semResposta = todos(p, ".tr-avg.muted");
    assert.ok(semResposta.length > 0);
    semResposta.forEach((el) => assert.equal(el.textContent, "sem resposta"));
  });
});

test("tracert do IOS não ganha a marca de caminho alternativo", () => {
  // o IOS imprime o next hop uma vez por salto, então não existe `addresses`
  comPagina(
    {
      type: "traceroute",
      nos: "cisco_ios",
      status: "success",
      destination: "1.1.1.1",
      reachable: true,
      hops: [
        { hop: 1, ip: "10.0.0.241", asn: 265269, times: [{ type: "reply", ms: 40 }], avg_ms: 40 },
      ],
    },
    (p) => {
      assert.equal($(p, ".tr-alt"), null);
      assert.equal(todos(p, ".tr-asn").length, 1);
      assert.match($(p, ".tr-asn").textContent, /AS 265269/);
    },
  );
});

// --- bgp prefix -----------------------------------------------------------

test("detalhe BGP VRP mostra AS local e router ID, que o VRP traz no lugar de versão", () => {
  comPagina(BGP, (p) => {
    assert.match($(p, ".bgp-sub").textContent, new RegExp(`AS ${BGP.local_as}`));
    assert.match($(p, ".bgp-sub").textContent, new RegExp(`router ID ${BGP.router_id}`));
    assert.doesNotMatch($(p, ".bgp-sub").textContent, /versão|tabela/);
  });
});

test("detalhe BGP VRP desenha os dois caminhos e marca o melhor", () => {
  comPagina(BGP, (p) => {
    assert.equal(todos(p, ".path-card").length, BGP.paths.length);
    assert.equal(todos(p, ".path-card.best").length, 1);
    assert.match($(p, ".path-more").textContent, /Outros caminhos \(1\)/);
  });
});

test("detalhe BGP VRP mostra o motivo da derrota do caminho perdedor", () => {
  comPagina(BGP, (p) => {
    const perdidos = todos(p, ".path-lost");
    assert.equal(perdidos.length, 1);
    assert.equal(BGP.paths[1].not_preferred, "router ID");
    assert.match(perdidos[0].textContent, /não preferido por router ID/);
  });
});

test("detalhe BGP VRP não repete o motivo da derrota como flag", () => {
  comPagina(BGP, (p) => {
    const flags = todos(p, ".path-card")[1].querySelector(".flags").textContent;
    assert.doesNotMatch(flags, /not preferred/);
    assert.match(flags, /valid/);
  });
});

test("detalhe BGP VRP mostra communities, interface e tempo na tabela", () => {
  comPagina(BGP, (p) => {
    const texto = todos(p, ".path-card")[0].textContent;
    BGP.paths[0].communities.forEach((c) => assert.ok(texto.includes(c), `faltou ${c}`));
    assert.ok(texto.includes(BGP.paths[0].out_interface));
    assert.ok(texto.includes(BGP.paths[0].duration));
  });
});

test("detalhe BGP VRP mostra pref-val pelo nome do VRP, sem virar métrica", () => {
  comPagina(BGP, (p) => {
    const origem = $(p, ".path-attrs dd.mono + div dd") || todos(p, ".path-attrs dd")[2];
    const texto = origem.textContent;
    assert.match(texto, new RegExp(`pref-val ${BGP.paths[0].pref_val}`));
    // `metric` é do IOS e o parser do VRP deixa em null de propósito: se ele
    // aparecesse aqui, a card estaria comparando grandezas diferentes
    assert.doesNotMatch(texto, /\bmetric\b/);
    assert.ok(BGP.paths.every((path) => path.metric === null));
  });
});

test("detalhe BGP VRP com agregação mostra AS e router-id do agregador", () => {
  comPagina(BGP_AGGR, (p) => {
    const linhas = todos(p, ".path-attrs div").filter((d) =>
      d.querySelector("dt").textContent === "Agregação",
    );
    assert.equal(linhas.length, BGP_AGGR.paths.length);
    const aggr = BGP_AGGR.paths[0].aggregations[0];
    assert.ok(linhas[0].textContent.includes(`AS${aggr.asn}`));
    assert.ok(linhas[0].textContent.includes(aggr.router));
  });
});

test("detalhe BGP VRP de prefixo inexistente avisa em vez de mostrar caminho", () => {
  comPagina(BGP_NAO_ENCONTRADO, (p) => {
    assert.match($(p, ".bgp-head .badge").textContent, /Não encontrado/);
    assert.match(p.texto(), /não está na tabela BGP/);
    assert.equal(todos(p, ".path-card").length, 0);
    assert.match($(p, ".bgp-prefix").textContent, /45\.0\.0\.0\/24/);
  });
});

test("detalhe BGP do IOS não ganha communities, interface nem motivo de derrota", () => {
  // payload no formato do IOS: nenhum dos campos que o VRP acrescenta
  comPagina(
    {
      type: "bgp_prefix",
      nos: "cisco_ios",
      status: "ok",
      prefix: "8.8.8.0/24",
      version: 100803950,
      table: "default",
      available: 1,
      best_index: 1,
      select: 1,
      advertised: false,
      update_groups: [],
      paths: [
        {
          index: 1,
          as_path_text: "263009 15169",
          as_path: ["263009", "15169"],
          aggregations: [],
          next_hop: "10.200.210.129",
          from: "10.200.210.129",
          origin: "IGP",
          localpref: 150,
          metric: 0,
          weight: 0,
          communities: [],
          not_preferred: null,
          flags: ["valid", "external", "best"],
          best: true,
        },
      ],
    },
    (p) => {
      assert.equal(todos(p, ".path-lost").length, 0);
      assert.match($(p, ".bgp-sub").textContent, /versão 100803950 · tabela default/);
      const texto = todos(p, ".path-card")[0].textContent;
      assert.doesNotMatch(texto, /Communities|pref-val/);
      // e a métrica do IOS continua aparecendo
      assert.match(texto, /metric 0/);
    },
  );
});

test("payload do VRP não injeta HTML pelas communities nem pelo motivo da derrota", () => {
  const hostil = JSON.parse(JSON.stringify(BGP));
  hostil.paths[0].communities = ['<img src=x onerror=alert(1)>'];
  hostil.paths[1].not_preferred = 'router ID"><script>alert(2)</script>';
  comPagina(hostil, (p) => {
    // a contagem é do resultado renderizado, não do documento: o index.html
    // tem `<script>` próprio e contá-los todos reprovaria sempre
    const saida = p.doc.getElementById("parsed");
    assert.equal(saida.querySelectorAll("img[onerror]").length, 0);
    assert.equal(saida.querySelectorAll("script").length, 0);
    // o texto tem de aparecer escapado, proving que virou texto e não nó
    assert.match(saida.textContent, /<img src=x onerror=alert\(1\)>/);
  });
});

// ---------------------------------------------------------------------------
// Busca por community
//
// O VRP é o único dos dois que traz a coluna `Community`: a saída mostra todas
// as communities de cada prefixo, e não tem coluna de AS path. A card é a mesma
// do IOS — a coluna `Communities` só entra quando o payload tem o dado.
// ---------------------------------------------------------------------------

const COMMUNITY = require("./fixtures/vrp_bgp_community.json");
const COMMUNITY_VAZIA = require("./fixtures/vrp_bgp_community_empty.json");

test("card de community do VRP lista os prefixos que usam a community", () => {
  comPagina(COMMUNITY, (p) => {
    assert.match(p.texto(), /65001:40100/);
    assert.match(p.texto(), /198\.51\.100\.0\/23/);
    assert.match(p.texto(), /198\.51\.100\.0\/24/);
    assert.match(p.texto(), /198\.51\.101\.0\/24/);
  });
});

test("card de community do VRP mostra a lista completa de cada prefixo", () => {
  comPagina(COMMUNITY, (p) => {
    // a /23 carrega 5 communities, incluindo a consultada
    assert.match(p.texto(), /65001:10301/);
    assert.match(p.texto(), /65001:10400/);
    assert.match(p.texto(), /65001:20100/);
    assert.match(p.texto(), /65001:1000/);
  });
});

test("card de community do VRP marca rota sem AS path como local", () => {
  comPagina(COMMUNITY, (p) => {
    assert.match(p.texto(), /local/);
    // sem coluna de AS path, o card não pode inventar um AS de origem
    assert.doesNotMatch(p.texto(), /AS 644960/);
  });
});

test("card de community do VRP mostra estado vazio quando a community não é usada", () => {
  comPagina(COMMUNITY_VAZIA, (p) => {
    assert.match(p.texto(), /Nenhum prefixo casou a community/);
    assert.match(p.texto(), /65000:1/);
  });
});

test("card de community do IOS não ganha a coluna Communities", () => {
  const IOS = {
    type: "bgp_community",
    community: "no-export",
    total_paths: 2,
    total_prefixes: 2,
    declared_total: null,
    origin_count: 1,
    best_paths: 2,
    origins: [
      {
        origin_as: "266136",
        is_local: false,
        next_hops: ["187.16.217.33"],
        next_hops_truncated: false,
        entries: [
          { prefix: "45.6.136.0/24", next_hop: "187.16.217.33", best: true },
          { prefix: "45.6.137.0/24", next_hop: "187.16.217.33", best: true },
        ],
        best_paths: 2,
        paths: 2,
        prefixes: ["45.6.136.0/24", "45.6.137.0/24"],
        prefixes_truncated: false,
      },
    ],
    capped_origins: false,
    capped_prefixes: false,
  };
  comPagina(IOS, (p) => {
    const cabecalhos = todos(p, "thead th").map((th) => th.textContent);
    assert.equal(cabecalhos.includes("Communities"), false);
    assert.match(p.texto(), /AS 266136/);
  });
});
