package io.github.zxcv718.wiki.domain;

import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;

@Entity
@Table(name = "groups")
public class Group extends AssignedIdEntity {

    @Id
    private String id;

    private String name;

    protected Group() {
    }

    public Group(String id, String name) {
        this.id = id;
        this.name = name;
    }

    @Override
    public String getId() {
        return id;
    }

    public String getName() {
        return name;
    }
}
