package io.github.zxcv718.wiki.web;

import static java.lang.annotation.ElementType.FIELD;
import static java.lang.annotation.ElementType.PARAMETER;
import static java.lang.annotation.ElementType.TYPE_USE;
import static java.lang.annotation.RetentionPolicy.RUNTIME;

import jakarta.validation.Constraint;
import jakarta.validation.Payload;
import jakarta.validation.ReportAsSingleViolation;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Pattern;
import java.lang.annotation.Retention;
import java.lang.annotation.Target;

/**
 * 권한 목록의 원소 형식: all, user:{id}, group:{id} (4장 "권한 모델"). Python 쪽 search/filters.py와 같은 규칙이다.
 * 목록 안의 null은 권한으로 해석할 수 없으므로 막는다.
 */
@NotNull
@Pattern(regexp = "^(all|(user|group):[A-Za-z0-9._-]+)$")
@Constraint(validatedBy = {})
@ReportAsSingleViolation
@Target({FIELD, PARAMETER, TYPE_USE})
@Retention(RUNTIME)
public @interface Principal {

    String message() default "principal은 all, user:{id}, group:{id} 중 하나여야 합니다.";

    Class<?>[] groups() default {};

    Class<? extends Payload>[] payload() default {};
}
