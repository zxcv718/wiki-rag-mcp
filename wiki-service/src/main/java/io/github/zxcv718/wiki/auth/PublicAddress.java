package io.github.zxcv718.wiki.auth;

import java.net.Inet4Address;
import java.net.InetAddress;
import java.net.UnknownHostException;
import java.util.List;

/**
 * CIMD 문서를 가져와도 되는 공인 주소인가. 내부망이나 서버 자신(클라우드 메타데이터 169.254.169.254 포함)으로 요청을
 * 보내게 하는 요청 위조(SSRF)를 막는다.
 *
 * IPv4는 IANA 특수 용도 주소를 빼고 모두 공인으로 본다. IPv6는 거꾸로 전역 유니캐스트(2000::/3)만 받고 그 안의 특수 용도
 * 대역을 뺀다. IPv6는 IPv4 주소를 품는 형식(NAT64, 6to4, Teredo)이 많아, 허용할 대역만 적는 편이 빠뜨릴 위험이 적다.
 * IPv4 대응 주소(::ffff:a.b.c.d)는 Java가 IPv4 주소로 바꿔 주므로 IPv4 규칙을 탄다.
 */
final class PublicAddress {

    private static final List<Cidr> NON_PUBLIC_V4 = List.of(
            cidr("0.0.0.0/8"),          // "이 네트워크"
            cidr("10.0.0.0/8"),         // 사설
            cidr("100.64.0.0/10"),      // 통신사 NAT
            cidr("127.0.0.0/8"),        // 루프백
            cidr("169.254.0.0/16"),     // 링크 로컬, 클라우드 메타데이터
            cidr("172.16.0.0/12"),      // 사설
            cidr("192.0.0.0/24"),       // IETF 프로토콜 할당
            cidr("192.0.2.0/24"),       // 문서용
            cidr("192.88.99.0/24"),     // 6to4 릴레이
            cidr("192.168.0.0/16"),     // 사설
            cidr("198.18.0.0/15"),      // 벤치마크
            cidr("198.51.100.0/24"),    // 문서용
            cidr("203.0.113.0/24"),     // 문서용
            cidr("224.0.0.0/4"),        // 멀티캐스트
            cidr("240.0.0.0/4"));       // 예약, 브로드캐스트

    private static final Cidr GLOBAL_UNICAST_V6 = cidr("2000::/3");

    private static final List<Cidr> NON_PUBLIC_V6 = List.of(
            cidr("2001::/23"),          // IETF 프로토콜 할당(Teredo 포함)
            cidr("2001:db8::/32"),      // 문서용
            cidr("2002::/16"),          // 6to4
            cidr("3fff::/20"));         // 문서용

    private PublicAddress() {
    }

    static boolean isPublic(InetAddress address) {
        if (address instanceof Inet4Address) {
            return NON_PUBLIC_V4.stream().noneMatch(range -> range.contains(address));
        }
        return GLOBAL_UNICAST_V6.contains(address) && NON_PUBLIC_V6.stream().noneMatch(range -> range.contains(address));
    }

    private record Cidr(byte[] prefix, int bits) {

        boolean contains(InetAddress address) {
            byte[] bytes = address.getAddress();
            if (bytes.length != prefix.length) {
                return false;
            }
            for (int i = 0; i < bits; i++) {
                int mask = 0x80 >>> (i % 8);
                if ((bytes[i / 8] & mask) != (prefix[i / 8] & mask)) {
                    return false;
                }
            }
            return true;
        }
    }

    /** 숫자 주소만 넣으므로 DNS 조회는 일어나지 않는다. */
    private static Cidr cidr(String notation) {
        String[] parts = notation.split("/");
        try {
            return new Cidr(InetAddress.getByName(parts[0]).getAddress(), Integer.parseInt(parts[1]));
        } catch (UnknownHostException e) {
            throw new IllegalArgumentException(notation, e);
        }
    }
}
