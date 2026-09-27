package io.github.zxcv718.wiki.web;

import static java.lang.annotation.ElementType.FIELD;
import static java.lang.annotation.ElementType.PARAMETER;
import static java.lang.annotation.ElementType.TYPE_USE;
import static java.lang.annotation.RetentionPolicy.RUNTIME;

import jakarta.validation.Constraint;
import jakarta.validation.Payload;
import jakarta.validation.ReportAsSingleViolation;
import jakarta.validation.constraints.Pattern;
import java.lang.annotation.Retention;
import java.lang.annotation.Target;

/**
 * 문서·스페이스·그룹·사용자 id 형식. principal의 id 부분과 같은 문자만 받아, id를 그대로 user:{id}, group:{id}로
 * 옮겨도 principal 형식이 깨지지 않게 한다. null은 통과하므로 필수 값에는 @NotNull을 함께 붙인다.
 *
 * 점으로만 된 id(".", "..")는 받지 않는다. URL 경로에 넣으면 경로 이동으로 해석돼 그 id로는 문서를 가리킬 수 없다.
 */
@Pattern(regexp = "^(?!\\.+$)[A-Za-z0-9._-]{1,64}$")
@Constraint(validatedBy = {})
@ReportAsSingleViolation
@Target({FIELD, PARAMETER, TYPE_USE})
@Retention(RUNTIME)
public @interface WikiId {

    String message() default "영문, 숫자, '.', '_', '-'로 된 1~64자여야 하고, 점으로만 될 수 없습니다.";

    Class<?>[] groups() default {};

    Class<? extends Payload>[] payload() default {};
}
