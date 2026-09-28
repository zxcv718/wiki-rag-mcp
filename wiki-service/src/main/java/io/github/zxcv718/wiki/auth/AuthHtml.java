package io.github.zxcv718.wiki.auth;

import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import org.springframework.http.HttpStatus;
import org.springframework.web.util.HtmlUtils;

/**
 * 인가 서버 화면(로그인, 동의, 오류)의 공통 틀. 템플릿 엔진 없이 HTML을 직접 쓰고, 넣는 값은 모두 이스케이프한다.
 *
 * 모양은 위키가 앱에 내주는 출입증이다. 솔잎색 머리띠에 목걸이 구멍을 두고, 동의 화면에서는 요청한 앱의 도메인을 가장
 * 크게 쓴다. 사용자가 반드시 확인해야 하는 값이기 때문이다. CSP(default-src 'none', 인라인 스타일만)라서 웹 글꼴과 이미지를
 * 쓰지 않고 OS의 한글 글꼴과 인라인 SVG만 쓴다. 밝은 화면과 어두운 화면을 모두 둔다.
 */
final class AuthHtml {

    /** 새솔(소나무) 표시. 줄기 하나에 가지 네 개. */
    private static final String MARK = """
            <svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2" \
            stroke-linecap="round" stroke-linejoin="round"><path d="M12 2.5v19M12 7 7.5 10.5M12 7l4.5 3.5\
            M12 11.5l-6 4.5M12 11.5l6 4.5"/></svg>""";

    static final String CHECK = """
            <svg viewBox="0 0 20 20" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2.2" \
            stroke-linecap="round" stroke-linejoin="round"><path d="m4.5 10.5 3.5 3.5 7.5-8"/></svg>""";

    static final String CROSS = """
            <svg viewBox="0 0 20 20" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2.2" \
            stroke-linecap="round"><path d="m6 6 8 8m0-8-8 8"/></svg>""";

    private static final String STYLE = """
            :root {
              color-scheme: light dark;
              --bg: #edf1ec; --surface: #fbfcfa; --ink: #15231c; --muted: #56655c; --line: #d3dcd5;
              --pine: #1d5a3f; --on-pine: #ffffff; --band: #1d5a3f; --on-band: #ffffff;
              --resin: #7a4e0e; --resin-bg: #fbf0d9; --error: #a1271c; --error-bg: #fbe9e6;
            }
            @media (prefers-color-scheme: dark) {
              :root {
                --bg: #0f1d17; --surface: #15271f; --ink: #e3ece6; --muted: #9db0a4; --line: #2a4136;
                --pine: #86c9a5; --on-pine: #0d1d15; --band: #24533f; --on-band: #e3ece6;
                --resin: #edb85c; --resin-bg: #2b2213; --error: #f19a8f; --error-bg: #34191a;
              }
            }
            * { box-sizing: border-box; }
            body {
              margin: 0; min-height: 100vh; background: var(--bg); color: var(--ink);
              font: 16px/1.6 -apple-system, BlinkMacSystemFont, "Apple SD Gothic Neo", "Malgun Gothic",
                "Noto Sans KR", system-ui, sans-serif;
              word-break: keep-all; -webkit-font-smoothing: antialiased;
            }
            main { width: min(100% - 2rem, 27rem); margin: clamp(1rem, 8vh, 5rem) auto 2rem; }
            .pass { position: relative; background: var(--surface); border: 1px solid var(--line); border-radius: 20px;
              overflow: hidden; }
            .pass > header { display: flex; align-items: center; gap: .5rem; padding: 2.25rem 1.5rem 1rem;
              background: var(--band); color: var(--on-band); font-weight: 700; font-size: 1.0625rem; }
            .pass > header::before { content: ""; position: absolute; top: .9rem; left: 50%; width: 3.5rem;
              height: .625rem; margin-left: -1.75rem; border-radius: 999px; background: var(--bg); }
            .pass > header svg { width: 1.375rem; height: 1.375rem; }
            .pass > div { padding: 1.5rem 1.5rem 1.75rem; }
            h1 { font-size: 1.5rem; line-height: 1.3; letter-spacing: -.02em; margin: 0 0 .5rem; }
            h1.host { font-size: clamp(2.25rem, 11vw, 3.125rem); font-weight: 800; line-height: 1.05;
              letter-spacing: -.04em; margin: .25rem 0 .5rem; overflow-wrap: anywhere; word-break: normal; }
            p { margin: 0 0 1rem; }
            .muted, .label, .name { color: var(--muted); font-size: .9375rem; }
            .label { margin: 0; }
            label { display: block; font-weight: 600; font-size: .9375rem; margin: 0 0 1rem; }
            input:not([type=hidden]) { display: block; width: 100%; height: 3rem; margin-top: .375rem; padding: 0 .875rem;
              font: inherit; font-weight: 400; color: var(--ink); background: var(--bg); border: 1px solid var(--line);
              border-radius: 12px; }
            input:focus { outline: none; border-color: var(--pine);
              box-shadow: 0 0 0 3px color-mix(in srgb, var(--pine) 28%, transparent); }
            button { display: block; width: 100%; height: 3rem; margin-top: .5rem; font: inherit; font-weight: 700;
              color: var(--on-pine); background: var(--pine); border: 1px solid var(--pine); border-radius: 12px;
              cursor: pointer; }
            button.secondary { color: var(--ink); background: transparent; border-color: var(--line); font-weight: 600; }
            :focus-visible { outline: 3px solid var(--pine); outline-offset: 2px; }
            .alert, .warning { padding: .75rem .875rem; border-radius: 12px; font-size: .9375rem; margin: 0 0 1rem; }
            .alert { color: var(--error); background: var(--error-bg); }
            .warning { color: var(--resin); background: var(--resin-bg); }
            ul.scopes { list-style: none; padding: 0; margin: 0 0 1.25rem; }
            ul.scopes li { display: flex; gap: .5rem; align-items: flex-start; margin: 0 0 .5rem; }
            ul.scopes svg { flex: none; width: 1.25rem; height: 1.25rem; margin-top: .2rem; color: var(--pine); }
            ul.scopes li.cannot, ul.scopes li.cannot svg { color: var(--muted); }
            .scope-id { display: block; color: var(--muted); font-size: .8125rem; }
            dl { margin: 0 0 1.25rem; padding-top: 1rem; border-top: 1px dashed var(--line); font-size: .9375rem; }
            dt { color: var(--muted); }
            dd { margin: 0 0 .625rem; overflow-wrap: anywhere; line-height: 1.45; }
            p.muted { line-height: 1.45; overflow-wrap: anywhere; }
            code { font: .875em/1.45 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
            .after { text-align: center; color: var(--muted); font-size: .875rem; margin: 1rem 0 0; }
            """;

    private AuthHtml() {
    }

    /** 출입증 모양의 화면. after는 출입증 아래에 작게 붙는 한 줄이다(없으면 빈 문자열). */
    static String page(String title, String body, String after) {
        return """
                <!doctype html>
                <html lang="ko">
                <head>
                <meta charset="utf-8">
                <meta name="viewport" content="width=device-width, initial-scale=1">
                <meta name="color-scheme" content="light dark">
                <title>%s - 새솔 위키</title>
                <style>
                %s</style>
                </head>
                <body>
                <main>
                <article class="pass">
                <header>%s새솔 위키</header>
                <div>
                %s
                </div>
                </article>
                %s
                </main>
                </body>
                </html>
                """.formatted(escape(title), STYLE, MARK, body, after);
    }

    /**
     * 브라우저가 보는 오류 화면. 인가 요청, 로그인, 동의는 모두 브라우저가 여는 주소라 Spring 기본 오류 응답(JSON이나
     * 영어 Whitelabel 화면) 대신 이 화면을 쓴다. 필터 체인 앞(AbuseGuard)에서도 쓰이므로 보안 헤더를 직접 붙인다.
     */
    static void sendError(HttpServletResponse response, HttpStatus status, String heading, String guidance,
                          String errorCode) throws IOException {
        response.setStatus(status.value());
        response.setContentType("text/html;charset=UTF-8");
        response.setHeader("Content-Security-Policy", AuthorizationServerConfig.CONTENT_SECURITY_POLICY);
        response.setHeader("X-Frame-Options", "DENY");
        response.setHeader("Cache-Control", "no-store");
        String code = errorCode == null ? "" : "<p class=\"muted\">오류 코드 <code>" + escape(errorCode) + "</code></p>";
        response.getWriter().write(page(heading, "<h1>" + escape(heading) + "</h1><p>" + escape(guidance) + "</p>" + code,
                ""));
    }

    static String escape(String value) {
        return HtmlUtils.htmlEscape(value);
    }
}
