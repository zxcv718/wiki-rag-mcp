package io.github.zxcv718.wiki.web;

import org.springframework.http.HttpStatus;

/** 오류 본문 {"error": ..., "message": ...}으로 바뀌는 예외. error 값은 README의 세 가지뿐이다. */
public class ApiException extends RuntimeException {

    private final HttpStatus status;
    private final String error;

    private ApiException(HttpStatus status, String error, String message) {
        super(message);
        this.status = status;
        this.error = error;
    }

    public static ApiException notFound(String message) {
        return new ApiException(HttpStatus.NOT_FOUND, "not_found", message);
    }

    public static ApiException conflict(String message) {
        return new ApiException(HttpStatus.CONFLICT, "conflict", message);
    }

    public static ApiException badRequest(String message) {
        return new ApiException(HttpStatus.BAD_REQUEST, "bad_request", message);
    }

    public HttpStatus status() {
        return status;
    }

    public ApiError body() {
        return new ApiError(error, getMessage());
    }
}
