// search_wiki 부하 측정 (README.md "방법"). 가상 사용자 VUS명이 생각하는 시간 없이 검색을 이어 보낸다.
// run.sh가 Docker의 k6로 돌린다. /tokens.json, /golden.jsonl, /out을 붙여 넣는다.
import http from 'k6/http';
import { SharedArray } from 'k6/data';
import { Rate } from 'k6/metrics';

const RESOURCE = __ENV.RESOURCE || 'https://mcp.dmssh.store/mcp';
const VUS = Number(__ENV.VUS);
const TOKENS = JSON.parse(open('/tokens.json'));
// 골든셋 질문을 그 질문을 한 사용자(asker)의 토큰으로 보낸다. 권한이 다른 사용자가 섞여 검색 필터가 매번 달라진다
const QUESTIONS = new SharedArray('golden', () =>
  open('/golden.jsonl').trim().split('\n').map((line) => JSON.parse(line)).map((q) => [q.question, q.asker]));

const failed = new Rate('search_failed');

export const options = {
  scenarios: { search: { executor: 'constant-vus', vus: VUS, duration: __ENV.DURATION } },
  summaryTrendStats: ['avg', 'min', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'],
};

// MCP Python SDK가 2026-07-28 방식으로 보내는 요청과 같은 헤더와 본문이다
const META = {
  'io.modelcontextprotocol/protocolVersion': '2026-07-28',
  'io.modelcontextprotocol/clientInfo': { name: 'wiki-rag-load', version: '1' },
  'io.modelcontextprotocol/clientCapabilities': {},
};

export default function () {
  const [question, asker] = QUESTIONS[Math.floor(Math.random() * QUESTIONS.length)];
  const body = JSON.stringify({
    jsonrpc: '2.0', id: 1, method: 'tools/call',
    params: { name: 'search_wiki', arguments: { query: question, top_k: 5 }, _meta: META },
  });
  const res = http.post(RESOURCE, body, {
    headers: {
      Authorization: `Bearer ${TOKENS[asker]}`,
      Accept: 'application/json, text/event-stream',
      'Content-Type': 'application/json',
      'MCP-Protocol-Version': '2026-07-28',
      'Mcp-Method': 'tools/call',
      'Mcp-Name': 'search_wiki',
    },
    timeout: '30s',
    tags: { name: 'search_wiki' },
  });
  let ok = res.status === 200;
  if (ok) {
    try {
      const reply = res.json();
      ok = !reply.error && reply.result !== undefined && reply.result.isError !== true;
    } catch (e) {
      ok = false;
    }
  }
  failed.add(!ok);
}

export function handleSummary(data) {
  const d = data.metrics.http_req_duration.values;
  const ms = (v) => `${Math.round(v)}ms`;
  const line = `동시 ${VUS}: 요청 ${data.metrics.http_reqs.values.count}개, ` +
    `${data.metrics.http_reqs.values.rate.toFixed(2)}/s, 오류율 ${(data.metrics.search_failed.values.rate * 100).toFixed(2)}%, ` +
    `p50 ${ms(d.med)}, p95 ${ms(d['p(95)'])}, p99 ${ms(d['p(99)'])}, 최대 ${ms(d.max)}\n`;
  return { [`/out/k6-${VUS}.json`]: JSON.stringify(data, null, 2), stdout: line };
}
