package io.github.zxcv718.wiki.web;

import static java.lang.annotation.ElementType.FIELD;
import static java.lang.annotation.ElementType.PARAMETER;
import static java.lang.annotation.RetentionPolicy.RUNTIME;

import jakarta.validation.Constraint;
import jakarta.validation.Payload;
import jakarta.validation.ReportAsSingleViolation;
import jakarta.validation.constraints.Pattern;
import java.lang.annotation.Retention;
import java.lang.annotation.Target;

/** 문서 등급 이름 (ADR-17). null은 통과하므로, 등급이 꼭 있어야 하는 곳에는 @NotNull을 함께 붙인다. */
@Pattern(regexp = "^(general|confidential)$")
@Constraint(validatedBy = {})
@ReportAsSingleViolation
@Target({FIELD, PARAMETER})
@Retention(RUNTIME)
public @interface ClassificationName {

    String message() default "general 또는 confidential이어야 합니다.";

    Class<?>[] groups() default {};

    Class<? extends Payload>[] payload() default {};
}
