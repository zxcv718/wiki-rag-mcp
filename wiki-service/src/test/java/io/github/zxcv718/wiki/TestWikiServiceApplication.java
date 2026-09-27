package io.github.zxcv718.wiki;

import org.springframework.boot.SpringApplication;

public class TestWikiServiceApplication {

	public static void main(String[] args) {
		SpringApplication.from(WikiServiceApplication::main).with(TestcontainersConfiguration.class).run(args);
	}

}
