package io.github.zxcv718.wiki.auth;

import java.net.URI;
import java.net.URISyntaxException;
import java.security.Principal;
import java.util.Map;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.security.oauth2.core.endpoint.OAuth2AuthorizationRequest;
import org.springframework.security.oauth2.core.endpoint.OAuth2ParameterNames;
import org.springframework.security.oauth2.server.authorization.OAuth2Authorization;
import org.springframework.security.oauth2.server.authorization.OAuth2AuthorizationService;
import org.springframework.security.oauth2.server.authorization.OAuth2TokenType;
import org.springframework.security.oauth2.server.authorization.client.RegisteredClient;
import org.springframework.security.oauth2.server.authorization.client.RegisteredClientRepository;
import org.springframework.security.web.csrf.CsrfToken;
import org.springframework.stereotype.Controller;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.ResponseBody;
import org.springframework.web.util.HtmlUtils;
import org.springframework.web.util.UriComponents;
import org.springframework.web.util.UriComponentsBuilder;

/**
 * 로그인 화면과 동의 화면. 편집 화면처럼 따로 만들 만한 크기가 아니라 템플릿 엔진 없이 HTML을 직접 쓴다. 화면에 넣는
 * 값은 모두 이스케이프한다. 특히 client_name은 CIMD 문서가 적은 값이라 누구나 정할 수 있다.
 *
 * 제출은 Spring Security가 받는다. 로그인은 POST /login(폼 로그인), 동의는 POST /oauth2/consent(AuthorizationServerConfig)다.
 */
@Controller
class AuthPages {

    private static final Map<String, String> SCOPE_DESCRIPTIONS =
            Map.of(AuthorizationRules.SCOPE, "볼 권한이 있는 위키 문서를 검색하고 읽습니다");

    private static final OAuth2TokenType STATE = new OAuth2TokenType(OAuth2ParameterNames.STATE);

    private final RegisteredClientRepository clients;
    private final OAuth2AuthorizationService authorizations;

    AuthPages(RegisteredClientRepository clients, OAuth2AuthorizationService authorizations) {
        this.clients = clients;
        this.authorizations = authorizations;
    }

    @GetMapping(value = AuthorizationServerConfig.LOGIN, produces = MediaType.TEXT_HTML_VALUE)
    @ResponseBody
    String login(@RequestParam(required = false) String error, CsrfToken csrf) {
        String alert = error == null ? "" : "<p class=\"alert\" role=\"alert\">아이디나 비밀번호가 맞지 않습니다.</p>";
        return page("위키 로그인", """
                <h1>위키 로그인</h1>
                %s
                <form method="post" action="/login">
                  <label>위키 사용자 id<input name="username" autocomplete="username" required autofocus></label>
                  <label>비밀번호<input type="password" name="password" autocomplete="current-password" required></label>
                  %s
                  <button type="submit">로그인</button>
                </form>
                """.formatted(alert, csrfField(csrf)));
    }

    /**
     * 누가 접근을 요청하는지는 client_id의 호스트로 보여 준다. 이름(client_name)은 누구나 적을 수 있지만 호스트는 그
     * 도메인을 가진 쪽만 쓸 수 있다. 사전 등록 클라이언트는 id가 주소가 아니라 id를 그대로 보인다.
     *
     * 화면의 값은 주소의 쿼리가 아니라 state로 찾은 진행 중인 인가에서 읽는다. 쿼리를 믿으면 다른 앱 이름을 보여 주는
     * 링크를 만들 수 있다. 허용하면 실제로 돌아갈 redirect_uri의 호스트도 보여 주고, client_id의 호스트와 다르면 경고한다(루프백은 제외).
     * 공용 저장소 호스트에 문서를 올려 믿을 만한 호스트로 보이게 하는 경우를 사용자가 알아볼 수 있게 하기 위해서다.
     */
    @GetMapping(value = AuthorizationServerConfig.CONSENT, produces = MediaType.TEXT_HTML_VALUE)
    @ResponseBody
    ResponseEntity<String> consent(@RequestParam String state, CsrfToken csrf, Principal user) {
        OAuth2Authorization authorization = inFlight(state, user);
        RegisteredClient client = authorization == null ? null : clients.findById(authorization.getRegisteredClientId());
        OAuth2AuthorizationRequest request = authorization == null ? null
                : authorization.getAttribute(OAuth2AuthorizationRequest.class.getName());
        if (client == null || request == null) {
            return ResponseEntity.status(HttpStatus.BAD_REQUEST).body(page("알 수 없는 요청",
                    "<h1>알 수 없는 요청입니다</h1><p>앱의 로그인 요청을 처음부터 다시 시작해 주세요.</p>"));
        }
        String clientId = client.getClientId();
        String redirectUri = request.getRedirectUri() != null ? request.getRedirectUri()
                : client.getRedirectUris().iterator().next();
        UriComponents redirect = UriComponentsBuilder.fromUriString(redirectUri).build();
        String redirectHost = String.valueOf(redirect.getHost());
        boolean loopback = AuthorizationRules.isLoopback(redirect);
        URI clientUri = metadataUri(clientId);

        StringBuilder details = new StringBuilder();
        if (!client.getClientName().equals(clientId)) {
            details.append("<p class=\"name\">앱이 밝힌 이름: ").append(escape(client.getClientName())).append("</p>");
        }
        details.append("<dl>");
        if (clientUri != null) {
            details.append("<dt>앱 주소</dt><dd><code>").append(escape(clientId)).append("</code></dd>");
        }
        details.append("<dt>허용하면 돌아갈 곳</dt><dd>");
        if (loopback) {
            details.append("이 컴퓨터에서 실행 중인 앱 ");
        }
        details.append("<code>").append(escape(redirectHost)).append("</code></dd></dl>");
        // 루프백으로 돌아가면 코드는 사용자 자신의 컴퓨터에 있는 앱이 받으므로 호스트가 달라도 경고하지 않는다.
        // Claude Code처럼 앱 주소는 claude.ai, 돌아갈 곳은 localhost인 네이티브 앱이 매번 경고를 보지 않게 하기 위해서다
        if (clientUri != null && !loopback && !clientUri.getHost().equalsIgnoreCase(redirectHost)) {
            details.append("<p class=\"warning\" role=\"alert\">앱 주소의 호스트(").append(escape(clientUri.getHost()))
                    .append(")와 돌아갈 곳의 호스트(").append(escape(redirectHost))
                    .append(")가 다릅니다. 믿을 수 있는 앱인지 확인한 뒤에 허용해 주세요.</p>");
        }
        StringBuilder scopes = new StringBuilder();
        StringBuilder scopeFields = new StringBuilder();
        for (String requested : request.getScopes().stream().sorted().toList()) {
            scopes.append("<li>").append(escape(SCOPE_DESCRIPTIONS.getOrDefault(requested, requested)))
                    .append(" <code>").append(escape(requested)).append("</code></li>");
            scopeFields.append(hidden("scope", requested));
        }
        String common = hidden("client_id", clientId) + hidden("state", state) + csrfField(csrf);
        return ResponseEntity.ok(page("접근 허용", """
                <p class="label">이 앱이 위키에 접근하려고 합니다</p>
                <h1 class="host">%s</h1>
                %s
                <p>%s님의 권한으로 아래 일을 할 수 있게 됩니다.</p>
                <ul>%s</ul>
                <form method="post" action="/oauth2/consent">%s%s<button type="submit">허용</button></form>
                <form method="post" action="/oauth2/consent">%s<button type="submit" class="secondary">거부</button></form>
                """.formatted(escape(hostOf(clientId)), details, escape(user.getName()), scopes, common, scopeFields,
                common)));
    }

    /** 이 사용자가 진행 중인 인가. 다른 사용자의 state면 없는 것으로 본다. */
    private OAuth2Authorization inFlight(String state, Principal user) {
        OAuth2Authorization authorization;
        try {
            authorization = authorizations.findByToken(state, STATE);
        } catch (RuntimeException e) {
            // 그사이 CIMD 문서를 다시 가져오지 못해 클라이언트를 찾을 수 없는 경우 등
            return null;
        }
        return authorization != null && authorization.getPrincipalName().equals(user.getName()) ? authorization : null;
    }

    private static URI metadataUri(String clientId) {
        try {
            URI uri = new URI(clientId);
            return uri.getHost() == null ? null : uri;
        } catch (URISyntaxException e) {
            return null;
        }
    }

    /** 주소 형식의 client_id(CIMD)는 호스트(포트가 있으면 포트까지), 그 밖에는 id 그대로. */
    static String hostOf(String clientId) {
        URI uri = metadataUri(clientId);
        if (uri == null) {
            return clientId;
        }
        return uri.getPort() == -1 ? uri.getHost() : uri.getHost() + ":" + uri.getPort();
    }

    private static String csrfField(CsrfToken csrf) {
        return hidden(csrf.getParameterName(), csrf.getToken());
    }

    private static String hidden(String name, String value) {
        return "<input type=\"hidden\" name=\"" + escape(name) + "\" value=\"" + escape(value) + "\">";
    }

    private static String escape(String value) {
        return HtmlUtils.htmlEscape(value);
    }

    private static String page(String title, String body) {
        return """
                <!doctype html>
                <html lang="ko">
                <head>
                <meta charset="utf-8">
                <meta name="viewport" content="width=device-width, initial-scale=1">
                <title>%s</title>
                <style>
                  body { font-family: system-ui, sans-serif; max-width: 26rem; margin: 3rem auto; padding: 0 1rem; color: #1f2328; }
                  label { display: block; margin: 0 0 1rem; }
                  input:not([type=hidden]) { display: block; width: 100%%; box-sizing: border-box; padding: .5rem; margin-top: .25rem; }
                  button { padding: .5rem 1.25rem; margin: .25rem 0; }
                  .host { font-size: 1.8rem; word-break: break-all; margin: .25rem 0; }
                  .label, .name { color: #59636e; font-size: .9rem; margin: 0; }
                  .alert, .warning { color: #b42318; }
                  dt { color: #59636e; font-size: .9rem; }
                  dd { margin: 0 0 .5rem; word-break: break-all; }
                  .secondary { background: none; }
                </style>
                </head>
                <body>
                <main>
                %s
                </main>
                </body>
                </html>
                """.formatted(escape(title), body);
    }
}
