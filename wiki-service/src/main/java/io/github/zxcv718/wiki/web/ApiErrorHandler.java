package io.github.zxcv718.wiki.web;

import io.github.zxcv718.wiki.domain.VersionConflictException;
import java.sql.SQLException;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import org.springframework.context.MessageSourceResolvable;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.http.ResponseEntity;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.validation.FieldError;
import org.springframework.validation.method.ParameterErrors;
import org.springframework.validation.method.ParameterValidationResult;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.method.annotation.HandlerMethodValidationException;
import org.springframework.web.method.annotation.MethodArgumentTypeMismatchException;
import org.springframework.web.servlet.resource.NoResourceFoundException;
import tools.jackson.core.JacksonException;
import tools.jackson.databind.exc.MismatchedInputException;
import tools.jackson.databind.exc.UnrecognizedPropertyException;

/** 예외를 README의 오류 본문으로 바꾼다. 필드 이름은 요청 JSON과 같게 snake_case로 적는다. */
@RestControllerAdvice
class ApiErrorHandler {

    private static final String UNIQUE_VIOLATION = "23505";

    @ExceptionHandler(ApiException.class)
    ResponseEntity<ApiError> handle(ApiException e) {
        return respond(e);
    }

    @ExceptionHandler(VersionConflictException.class)
    ResponseEntity<ApiError> handle(VersionConflictException e) {
        return respond(ApiException.conflict(e.getMessage()));
    }

    /**
     * DB가 거절한 쓰기. 같은 id가 이미 있을 때(unique 위반, SQLSTATE 23505)만 409다. 앞에서 확인했더라도 동시에 들어온
     * 요청이 같은 id를 먼저 만들 수 있어, 이때는 DB의 기본 키가 막는다. 저장할 수 없는 값(본문의 NUL 문자 같은 22xxx)이나
     * 다른 제약 위반(23xxx)은 요청을 고쳐야 하는 문제라 400이다. 그 밖의 오류는 서버 문제이므로 500으로 둔다.
     */
    @ExceptionHandler(DataIntegrityViolationException.class)
    ResponseEntity<ApiError> handle(DataIntegrityViolationException e) {
        String sqlState = sqlState(e);
        if (e instanceof DuplicateKeyException || UNIQUE_VIOLATION.equals(sqlState)) {
            return respond(ApiException.conflict("이미 있는 항목과 겹칩니다."));
        }
        if (sqlState != null && (sqlState.startsWith("22") || sqlState.startsWith("23"))) {
            return respond(ApiException.badRequest("저장할 수 없는 값이 있습니다. 본문에 NUL 문자처럼 허용되지 않는 문자가"
                    + " 없는지 확인해 주세요."));
        }
        throw e;
    }

    @ExceptionHandler(MethodArgumentNotValidException.class)
    ResponseEntity<ApiError> handle(MethodArgumentNotValidException e) {
        List<String> messages = new ArrayList<>();
        for (FieldError error : e.getBindingResult().getFieldErrors()) {
            messages.add(snakeCase(error.getField()) + ": " + error.getDefaultMessage());
        }
        return badRequest(messages);
    }

    @ExceptionHandler(HandlerMethodValidationException.class)
    ResponseEntity<ApiError> handle(HandlerMethodValidationException e) {
        List<String> messages = new ArrayList<>();
        for (ParameterValidationResult result : e.getParameterValidationResults()) {
            if (result instanceof ParameterErrors errors) {
                for (FieldError error : errors.getFieldErrors()) {
                    messages.add(snakeCase(error.getField()) + ": " + error.getDefaultMessage());
                }
                continue;
            }
            String parameter = snakeCase(result.getMethodParameter().getParameterName());
            for (MessageSourceResolvable error : result.getResolvableErrors()) {
                messages.add(parameter + ": " + error.getDefaultMessage());
            }
        }
        return badRequest(messages);
    }

    /** JSON을 읽지 못했다. 어느 필드가 문제인지 알 수 있으면 알려 준다. 모르는 필드는 오타일 수 있어 거절한다. */
    @ExceptionHandler(HttpMessageNotReadableException.class)
    ResponseEntity<ApiError> handle(HttpMessageNotReadableException e) {
        if (e.getCause() instanceof UnrecognizedPropertyException unknown) {
            return respond(ApiException.badRequest("알 수 없는 필드입니다: " + fieldPath(unknown)));
        }
        if (e.getCause() instanceof MismatchedInputException mismatch && !fieldPath(mismatch).isEmpty()) {
            return respond(ApiException.badRequest(fieldPath(mismatch) + ": 값이 빠졌거나 형식이 틀렸습니다."));
        }
        return respond(ApiException.badRequest("요청 본문을 읽을 수 없습니다. JSON 형식과 필드 이름, 값의 형식을 확인해 주세요."));
    }

    @ExceptionHandler(MethodArgumentTypeMismatchException.class)
    ResponseEntity<ApiError> handle(MethodArgumentTypeMismatchException e) {
        return respond(ApiException.badRequest(snakeCase(e.getName()) + ": 값의 형식이 틀렸습니다."));
    }

    @ExceptionHandler(NoResourceFoundException.class)
    ResponseEntity<ApiError> handle(NoResourceFoundException e) {
        return respond(ApiException.notFound("없는 경로입니다."));
    }

    private static ResponseEntity<ApiError> badRequest(List<String> messages) {
        messages.sort(null);
        return respond(ApiException.badRequest(String.join("; ", messages)));
    }

    private static ResponseEntity<ApiError> respond(ApiException e) {
        return ResponseEntity.status(e.status()).body(e.body());
    }

    /** 예외가 가리키는 JSON 위치 (documents[3].restricted_principals). JSON의 이름이라 이미 snake_case다. */
    private static String fieldPath(JacksonException e) {
        StringBuilder path = new StringBuilder();
        for (JacksonException.Reference reference : e.getPath()) {
            if (reference.getPropertyName() != null) {
                path.append(path.isEmpty() ? "" : ".").append(reference.getPropertyName());
            } else if (reference.getIndex() >= 0) {
                path.append('[').append(reference.getIndex()).append(']');
            }
        }
        return path.toString();
    }

    private static String sqlState(Throwable e) {
        for (Throwable cause = e; cause != null; cause = cause.getCause()) {
            if (cause instanceof SQLException sql) {
                return sql.getSQLState();
            }
        }
        return null;
    }

    static String snakeCase(String name) {
        return name == null ? "" : name.replaceAll("([a-z0-9])([A-Z])", "$1_$2").toLowerCase(Locale.ROOT);
    }
}
