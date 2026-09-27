package io.github.zxcv718.wiki;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.content;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import org.junit.jupiter.api.Test;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;

/** 토큰 없이, 틀린 토큰으로, 다른 경로의 토큰으로 부르면 모두 본문 없는 401이다. */
class AuthTest extends IntegrationTest {

    @Test
    void missingTokenIsRejected() throws Exception {
        mvc.perform(get("/internal/spaces"))
                .andExpect(status().isUnauthorized())
                .andExpect(content().string(""));
    }

    @Test
    void wrongTokenIsRejected() throws Exception {
        mvc.perform(get("/internal/spaces").header(HttpHeaders.AUTHORIZATION, "Bearer nope"))
                .andExpect(status().isUnauthorized())
                .andExpect(content().string(""));
        mvc.perform(get("/internal/spaces").header(HttpHeaders.AUTHORIZATION, SERVICE_TOKEN))
                .andExpect(status().isUnauthorized());
    }

    @Test
    void serviceTokenDoesNotOpenAdmin() throws Exception {
        asService(post("/admin/import").contentType(MediaType.APPLICATION_JSON).content(FIXTURE))
                .andExpect(status().isUnauthorized())
                .andExpect(content().string(""));
    }

    /** 경로 매개변수(;)를 붙여 접두사 검사를 피하려 해도 관리자 경로로 판단한다. */
    @Test
    void serviceTokenDoesNotOpenAdminThroughPathParameters() throws Exception {
        asService(post("/admin;x=1/import").contentType(MediaType.APPLICATION_JSON).content(FIXTURE))
                .andExpect(status().isUnauthorized());
    }

    @Test
    void adminTokenDoesNotOpenInternal() throws Exception {
        asAdmin(get("/internal/spaces"))
                .andExpect(status().isUnauthorized())
                .andExpect(content().string(""));
    }

    @Test
    void serviceTokenOpensInternal() throws Exception {
        getAsService("/internal/spaces")
                .andExpect(status().isOk())
                .andExpect(content().json("{\"spaces\": []}"));
    }

    /** 공개 경로가 없으므로 모르는 경로도 토큰부터 요구하고, 토큰이 맞으면 README 형식의 404를 준다. */
    @Test
    void unknownPathNeedsTokenAndAnswersNotFound() throws Exception {
        mvc.perform(get("/nowhere")).andExpect(status().isUnauthorized());
        getAsService("/nowhere")
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.error").value("not_found"));
    }
}
