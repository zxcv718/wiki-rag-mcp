package io.github.zxcv718.wiki.admin;

import io.github.zxcv718.wiki.admin.AdminRequests.CreateDocument;
import io.github.zxcv718.wiki.admin.AdminRequests.DocumentClassification;
import io.github.zxcv718.wiki.admin.AdminRequests.EditContent;
import io.github.zxcv718.wiki.admin.AdminRequests.Import;
import io.github.zxcv718.wiki.admin.AdminRequests.Principals;
import io.github.zxcv718.wiki.admin.AdminRequests.SpaceClassification;
import io.github.zxcv718.wiki.admin.AdminRequests.UserName;
import io.github.zxcv718.wiki.admin.AdminResponses.ImportResult;
import io.github.zxcv718.wiki.admin.AdminResponses.SpaceState;
import io.github.zxcv718.wiki.admin.AdminResponses.UserState;
import io.github.zxcv718.wiki.web.DocumentState;
import io.github.zxcv718.wiki.web.DocumentTombstone;
import io.github.zxcv718.wiki.web.WikiId;
import jakarta.validation.Valid;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;

/** 관리자 토큰으로 부르는 쓰기 API (README "관리자 API"). 편집 화면이 없어 문서를 고치는 경로는 이것 하나다. */
@RestController
@RequestMapping("/admin")
class AdminController {

    private final WikiAdminService admin;

    AdminController(WikiAdminService admin) {
        this.admin = admin;
    }

    @PostMapping("/import")
    ImportResult importWiki(@RequestBody @Valid Import body) {
        return admin.importWiki(body);
    }

    @PostMapping("/documents")
    @ResponseStatus(HttpStatus.CREATED)
    DocumentState createDocument(@RequestBody @Valid CreateDocument body) {
        return admin.createDocument(body);
    }

    @PutMapping("/documents/{doc_id}")
    DocumentState editContent(@PathVariable("doc_id") @WikiId String docId, @RequestBody @Valid EditContent body) {
        return admin.editContent(docId, body.title(), body.body(), body.baseVersion());
    }

    @PutMapping("/documents/{doc_id}/restrictions")
    DocumentState changeRestrictions(@PathVariable("doc_id") @WikiId String docId,
                                     @RequestBody @Valid Principals body) {
        return admin.changeRestrictions(docId, body.principals());
    }

    @PutMapping("/documents/{doc_id}/classification")
    DocumentState changeClassification(@PathVariable("doc_id") @WikiId String docId,
                                       @RequestBody @Valid DocumentClassification body) {
        return admin.changeClassification(docId, body.classification());
    }

    @DeleteMapping("/documents/{doc_id}")
    DocumentTombstone deleteDocument(@PathVariable("doc_id") @WikiId String docId) {
        return admin.deleteDocument(docId);
    }

    @PutMapping("/spaces/{space}/viewers")
    SpaceState changeSpaceViewers(@PathVariable("space") @WikiId String space, @RequestBody @Valid Principals body) {
        return admin.changeSpaceViewers(space, body.principals());
    }

    @PutMapping("/spaces/{space}/classification")
    SpaceState changeSpaceClassification(@PathVariable("space") @WikiId String space,
                                         @RequestBody @Valid SpaceClassification body) {
        return admin.changeSpaceClassification(space, body.classification());
    }

    @PutMapping("/users/{user_id}")
    UserState putUser(@PathVariable("user_id") @WikiId String userId, @RequestBody @Valid UserName body) {
        return admin.putUser(userId, body.name());
    }

    @PutMapping("/groups/{group}/members/{user_id}")
    UserState addMember(@PathVariable("group") @WikiId String group, @PathVariable("user_id") @WikiId String userId) {
        return admin.addMember(group, userId);
    }

    @DeleteMapping("/groups/{group}/members/{user_id}")
    UserState removeMember(@PathVariable("group") @WikiId String group,
                           @PathVariable("user_id") @WikiId String userId) {
        return admin.removeMember(group, userId);
    }
}
