package io.github.zxcv718.wiki.auth;

import static org.assertj.core.api.Assertions.assertThat;

import java.net.InetAddress;
import java.net.UnknownHostException;
import java.util.List;
import org.junit.jupiter.api.Test;

class PublicAddressTest {

    @Test
    void publicUnicastIsAccepted() throws UnknownHostException {
        for (String ip : List.of("8.8.8.8", "93.184.216.34", "2606:4700:4700::1111", "2a00:1450:4001::1")) {
            assertThat(PublicAddress.isPublic(InetAddress.getByName(ip))).as(ip).isTrue();
        }
    }

    /** IPv4를 품는 IPv6 형식(대응 주소, NAT64, 6to4, Teredo)으로 내부 주소를 가리켜도 막는다. */
    @Test
    void specialPurposeAddressesAreRejected() throws UnknownHostException {
        for (String ip : List.of("10.1.2.3", "127.0.0.1", "169.254.169.254", "172.31.255.255", "192.168.0.1",
                "100.64.0.1", "0.0.0.0", "224.0.0.1", "255.255.255.255", "198.18.0.1", "192.0.2.1",
                "::1", "::", "fc00::1", "fd00:ec2::254", "fe80::1", "ff02::1", "::ffff:10.0.0.1",
                "64:ff9b::a00:1", "2002:a00:1::", "2001:0:4136:e378::1", "2001:db8::1")) {
            assertThat(PublicAddress.isPublic(InetAddress.getByName(ip))).as(ip).isFalse();
        }
    }
}
