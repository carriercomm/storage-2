package eu.stratuslab.storage.disk.utils;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileNotFoundException;
import java.io.IOException;
import java.io.InputStream;
import java.math.BigInteger;
import java.util.ArrayList;
import java.util.Calendar;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.logging.Logger;

import org.restlet.data.Status;
import org.restlet.resource.ResourceException;

import eu.stratuslab.storage.disk.backend.BackEndStorage;
import eu.stratuslab.storage.disk.main.RootApplication;
import eu.stratuslab.storage.disk.main.ServiceConfiguration;
import eu.stratuslab.storage.persistence.Disk;
import eu.stratuslab.storage.persistence.Disk.DiskType;

/**
 * For unit tests see {@link DiskUtilsTest}
 * 
 */
public final class DiskUtils {

    private static final Logger LOGGER = Logger.getLogger("org.restlet");

    private DiskUtils() {

    }

    private static BackEndStorage getDiskStorage() {

        return new BackEndStorage();

    }

    public static String getTurl(String diskUuid) {
        BackEndStorage backend = getDiskStorage();
        return backend.getTurl(diskUuid);
    }

    public static void createDisk(Disk disk) {

        BackEndStorage diskStorage = getDiskStorage();

        diskStorage.create(disk.getUuid(), disk.getSize());
        diskStorage.map(disk.getUuid());

        disk.store();

    }

    public static Disk createMachineImageCoWDisk(Disk disk) {

        BackEndStorage diskStorage = getDiskStorage();

        Disk cowDisk = createCowDisk(disk);

        diskStorage.createCopyOnWrite(disk.getUuid(), cowDisk.getUuid(),
                disk.getSize());

        cowDisk.setType(DiskType.MACHINE_IMAGE_LIVE);
        diskStorage.map(disk.getUuid());

        cowDisk.store();

        return cowDisk;
    }

    protected static Disk createCowDisk(Disk disk) {
        Disk cowDisk = new Disk();
        cowDisk.setType(DiskType.DATA_IMAGE_LIVE);
        cowDisk.setBaseDiskUuid(disk.getUuid());
        cowDisk.setSize(disk.getSize());
        cowDisk.setIdentifier("snapshot:" + disk.getUuid());
        return cowDisk;
    }

    public static String rebaseDisk(Disk disk) {

        BackEndStorage diskStorage = getDiskStorage();

        return diskStorage.rebase(disk);
    }

    public static void removeDisk(String uuid) {
        getDiskStorage().unmap(uuid);
        getDiskStorage().delete(uuid);
    }

    public static String getDiskId(String host, int port, String uuid) {
        return String.format("pdisk:%s:%d:%s", host, port, uuid);
    }

    public static void attachHotplugDisk(String serviceName, int servicePort,
            String node, String vmId, String diskUuid, String target,
            String turl) {

        // Do NOT use the --register flag here. This may cause an infinite loop
        // in the process because it calls the pdisk service again.

        List<String> cmd = createHotPlugCommand(node);
        cmd.add("--op up");

        cmd.add("--attach");
        cmd.add("--link");
        cmd.add("--mount");

        cmd.add("--pdisk-id");
        cmd.add(getDiskId(serviceName, servicePort, diskUuid));

        cmd.add("--target");
        cmd.add(target);

        cmd.add("--vm-id");
        cmd.add(vmId);

        cmd.add("--turl");
        cmd.add(turl);

        cmd.add("--vm-disk-name");
        cmd.add(getDiskId(serviceName, servicePort, diskUuid));

        ProcessBuilder pb = new ProcessBuilder(cmd);
        ProcessUtils.execute(pb, "Unable to attach persistent disk");
    }

    public static String attachHotplugDisk(String diskUuid) {
        int port = ServiceConfiguration.getInstance().PDISK_SERVER_PORT;
        String host = "localhost";
        String tmpVmId = DiskUtils.generateUUID();

        String turl = getTurl(diskUuid);

        // FIXME: host is most probably wrong for the last parameter
        attachHotplugDisk(host, port, host, tmpVmId, diskUuid, host, turl);

        return tmpVmId;
    }

    protected static String getDiskLocation(String vmId, String diskUuid) {
        String attachedDisk = RootApplication.CONFIGURATION.CLOUD_NODE_VM_DIR
                + "/" + vmId + "/images/pdisk-" + diskUuid;
        return attachedDisk;
    }

    public static void detachHotplugDisk(String serviceName, int servicePort,
            String node, String vmId, String diskUuid, String target,
            String turl) {

        // Do NOT use the --register flag here. This may cause an infinite loop
        // in the process because it calls the pdisk service again.

        List<String> cmd = createHotPlugCommand(node);
        cmd.add("--op down");

        cmd.add("--attach");
        cmd.add("--link");
        cmd.add("--mount");

        cmd.add("--pdisk-id");
        cmd.add(getDiskId(serviceName, servicePort, diskUuid));

        cmd.add("--target");
        cmd.add(target);

        cmd.add("--vm-id");
        cmd.add(vmId);

        cmd.add("--turl");
        cmd.add(turl);

        cmd.add("--vm-disk-name");
        cmd.add(getDiskId(serviceName, servicePort, diskUuid));

        ProcessBuilder pb = new ProcessBuilder(cmd);
        ProcessUtils.execute(pb, "Unable to detach persistent disk");
    }

    protected static List<String> createHotPlugCommand(String node) {
        List<String> cmd = new ArrayList<String>();
        cmd.add("ssh");
        cmd.add("-p");
        cmd.add("22");
        cmd.add("-o");
        cmd.add("ConnectTimeout=5");
        cmd.add("-o");
        cmd.add("StrictHostKeyChecking=no");
        cmd.add("-i");
        cmd.add(RootApplication.CONFIGURATION.CLOUD_NODE_SSH_KEY);
        cmd.add(RootApplication.CONFIGURATION.CLOUD_NODE_ADMIN + "@" + node);
        cmd.add("/usr/sbin/stratus-pdisk-client.py");
        return cmd;
    }

    public static String generateUUID() {
        return UUID.randomUUID().toString();
    }

    public static String calculateHash(String uuid)
            throws FileNotFoundException {

        InputStream fis = null;// = new FileInputStream(getDevicePath() + uuid);

        return calculateHash(fis);

    }

    public static String calculateHash(File file) throws FileNotFoundException {

        InputStream fis = new FileInputStream(file);

        return calculateHash(fis);

    }

    public static String calculateHash(InputStream fis)
            throws FileNotFoundException {

        Map<String, BigInteger> info = MetadataUtils.streamInfo(fis);

        BigInteger sha1Digest = info.get("SHA-1");

        String identifier = MetadataUtils.sha1ToIdentifier(sha1Digest);

        return identifier;

    }

    public static String getDevicePath() {
        return "";// RootApplication.CONFIGURATION.LVM_GROUP_PATH + "/";
    }

    public static void createAndPopulateDiskLocal(Disk disk) {

        String uuid = disk.getUuid();

        File cachedDiskFile = FileUtils.getCachedDiskFile(uuid);
        String cachedDisk = cachedDiskFile.getAbsolutePath();
        try {

            createDisk(disk);

            try {
                copyContentsToVolume(uuid, cachedDisk);

                // Size has already been set on the disk.
                disk.setType(DiskType.DATA_IMAGE_RAW_READONLY);
                disk.setSeed(true);

            } catch (RuntimeException e) {
                removeDisk(disk.getUuid());
            }

        } finally {
            if (!cachedDiskFile.delete()) {
                LOGGER.warning("could not delete upload cache file: "
                        + cachedDisk);
            }
        }
    }

    private static void copyContentsToVolume(String uuid, String cachedDisk) {
        String diskLocation = attachDiskToThisHost(uuid);
        try {
            FileUtils.copyFile(cachedDisk, diskLocation);
        } finally {
            detachDiskFromThisHost(uuid);
            getDiskStorage().unmap(uuid);
        }
    }

    public static void copyUrlToVolume(String uuid, String url)
            throws IOException {
        String diskLocation = attachDiskToThisHost(uuid);
        try {
            DownloadUtils.copyUrlContentsToFile(url, new File(diskLocation));
        } finally {
            detachDiskFromThisHost(uuid);
            getDiskStorage().unmap(uuid);
        }
    }

    // FIXME: This always rounds up and does it starting from bytes.
    // Need to round up only starting from megabytes?
    public static long convertBytesToGigaBytes(long sizeInBytes) {
        double bytesInAGB = 1024 * 1024 * 1024;
        long inGB = (long) Math.ceil(sizeInBytes / bytesInAGB);
        return (inGB == 0 ? 1 : inGB);
    }

    public static void createCompressedDisk(String uuid) {

        String diskLocation = attachDiskToThisHost(uuid);

        ProcessBuilder pb = new ProcessBuilder("/bin/sh", "-c",
                RootApplication.CONFIGURATION.GZIP_CMD + " -f -c "
                        + diskLocation + " > "
                        + getCompressedDiskLocation(uuid));
        ProcessUtils.execute(pb, "Unable to compress disk " + uuid);

        detachDiskFromThisHost(uuid);
    }

    private static String attachDiskToThisHost(String uuid) {

        String host = "localhost";
        int port = ServiceConfiguration.getInstance().PDISK_SERVER_PORT;

        String linkName = getLinkedVolumeInDownloadCache(uuid);

        String turl = getTurl(uuid);

        List<String> cmd = getCommandAttachAndLinkLocal(uuid, host, port,
                linkName, turl);

        ProcessBuilder pb = new ProcessBuilder(cmd);
        ProcessUtils.execute(pb, "Unable to attach persistent disk");

        return linkName;
    }

    private static void detachDiskFromThisHost(String uuid) {
        unlinkVolumeFromDownloadCache(uuid);

        String host = "localhost";
        int port = ServiceConfiguration.getInstance().PDISK_SERVER_PORT;

        BackEndStorage backend = getDiskStorage();
        String turl = backend.getTurl(uuid);

        List<String> cmd = getCommandDetachLocal(uuid, host, port, turl);

        ProcessBuilder pb = new ProcessBuilder(cmd);
        ProcessUtils.execute(pb, "Unable to detach persistent disk");
    }

    private static void unlinkVolumeFromDownloadCache(String uuid) {
        String linkName = getLinkedVolumeInDownloadCache(uuid);
        File file = new File(linkName);
        if (!file.delete()) {
            throw new ResourceException(Status.SERVER_ERROR_INTERNAL,
                    "Failed deleting linked file: " + linkName);
        }
    }

    private static List<String> getCommandAttachAndLinkLocal(String uuid,
            String host, int port, String linkName, String turl) {
        List<String> cmd = new ArrayList<String>();

        cmd.add("/usr/sbin/stratus-pdisk-client.py");

        cmd.add("--op");
        cmd.add("up");

        cmd.add("--attach");

        cmd.add("--pdisk-id");
        cmd.add(getDiskId(host, port, uuid));

        cmd.add("--link-to");
        cmd.add(linkName);

        cmd.add("--turl");
        cmd.add(turl);

        return cmd;
    }

    private static List<String> getCommandDetachLocal(String uuid, String host,
            int port, String turl) {
        List<String> cmd = new ArrayList<String>();

        cmd.add("/usr/sbin/stratus-pdisk-client.py");

        cmd.add("--op");
        cmd.add("down");

        cmd.add("--attach");

        cmd.add("--pdisk-id");
        cmd.add(getDiskId(host, port, uuid));

        cmd.add("--turl");
        cmd.add(turl);

        return cmd;
    }

    private static String getLinkedVolumeInDownloadCache(String uuid) {
        return RootApplication.CONFIGURATION.CACHE_LOCATION + "/" + uuid
                + ".link";
    }

    public static String getCompressedDiskLocation(String uuid) {
        return RootApplication.CONFIGURATION.CACHE_LOCATION + "/" + uuid
                + ".gz";
    }

    public static Boolean isCompressedDiskBuilding(String uuid) {
        return FileUtils.isCachedDiskExists(uuid);
    }

    public static Boolean hasCompressedDiskExpire(String uuid) {
        File zip = new File(FileUtils.getCompressedDiskLocation(uuid));
        return hasCompressedDiskExpire(zip);
    }

    public static Boolean hasCompressedDiskExpire(File disk) {
        Calendar cal = Calendar.getInstance();
        return (cal.getTimeInMillis() > (disk.lastModified() + ServiceConfiguration.CACHE_EXPIRATION_DURATION));
    }

}
