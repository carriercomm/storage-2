/*
 Created as part of the StratusLab project (http://stratuslab.eu),
 co-funded by the European Commission under the Grant Agreement
 INSFO-RI-261552.

 Copyright (c) 2011, Centre National de la Recherche Scientifique (CNRS)

 Licensed under the Apache License, Version 2.0 (the "License");
 you may not use this file except in compliance with the License.
 You may obtain a copy of the License at

 http://www.apache.org/licenses/LICENSE-2.0

 Unless required by applicable law or agreed to in writing, software
 distributed under the License is distributed on an "AS IS" BASIS,
 WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 See the License for the specific language governing permissions and
 limitations under the License.
 */
package eu.stratuslab.storage.disk.main;

import eu.stratuslab.storage.disk.resources.DiskResource;
import eu.stratuslab.storage.disk.resources.DisksResource;
import eu.stratuslab.storage.disk.resources.HomeResource;
import eu.stratuslab.storage.disk.resources.InstancesResource;
import eu.stratuslab.storage.disk.resources.MountResource;
import eu.stratuslab.storage.disk.resources.MountsResource;
import eu.stratuslab.storage.disk.resources.TurlResource;
import eu.stratuslab.storage.disk.utils.DummyVerifier;
import freemarker.template.Configuration;
import org.restlet.Application;
import org.restlet.Component;
import org.restlet.Context;
import org.restlet.Request;
import org.restlet.Response;
import org.restlet.Restlet;
import org.restlet.data.ChallengeScheme;
import org.restlet.data.LocalReference;
import org.restlet.data.MediaType;
import org.restlet.data.Protocol;
import org.restlet.ext.freemarker.ContextTemplateLoader;
import org.restlet.resource.Directory;
import org.restlet.routing.Router;
import org.restlet.routing.TemplateRoute;
import org.restlet.security.ChallengeAuthenticator;

import java.util.Map;
import java.util.logging.Logger;

public class RootApplication extends Application {

    private static final Logger logger = Logger.getLogger("org.restlet");

    public static final String SVC_CONFIGURATION_KEY = "PDISK_SVC_CONFIG";
    public static final String FM_CONFIGURATION_KEY = "PDISK_FM_CONFIG";

    public static final ServiceConfiguration CONFIGURATION = ServiceConfiguration.getInstance();

    private Configuration freeMarkerConfiguration = null;

    public static void main(String[] args) throws Exception {

        Component component = new Component();

        component.getServers().add(Protocol.HTTP, ServiceConfiguration.getInstance().PDISK_SERVER_PORT);
        component.getServers().add(Protocol.FILE);
        component.getClients().add(Protocol.FILE);
        component.getClients().add(Protocol.CLAP);
        Application rootApplication = new RootApplication();
        component.getDefaultHost().attach("", rootApplication);

        try {
            component.start();
        } catch (Exception e) {
            e.printStackTrace();
            logger.severe("Starting StratusLab Storage Server FAILED!");
            System.exit(1);
        }
        logger.info("StratusLab Storage Server started!");
    }

    public RootApplication() {
        setName("StratusLab Persistent Disk Server");
        setDescription("StratusLab server for persistent disk storage.");
        setOwner("StratusLab");
        setAuthor("StratusLab");

        setStatusService(new CommonStatusService());

        getTunnelService().setUserAgentTunnel(true);

        getMetadataService().addExtension("gzip", MediaType.APPLICATION_GNU_ZIP, true);
    }

    @Override
    public Restlet createInboundRoot() {
        Context context = getContext();

        freeMarkerConfiguration = createFreeMarkerConfig(context);

        ServiceConfiguration configuration = ServiceConfiguration.getInstance();
        Router router = new RootRouter(context, configuration, freeMarkerConfiguration);

        TemplateRoute route = router
                .attach("/disks/{uuid}/mounts/{mountid}?metadata_only={" + MountResource
                                .METADATA_ONLY_QUERY_PARAMETER + "}",
                        MountResource.class);
        route.setMatchingQuery(true);

        router.attach("/disks/{uuid}/mounts/{mountid}/", MountResource.class);
        router.attach("/disks/{uuid}/mounts/{mountid}", MountResource.class);

        router.attach("/disks/{uuid}/mounts/", MountsResource.class);
        router.attach("/disks/{uuid}/mounts", MountsResource.class);

        router.attach("/disks/{uuid}/turl/", TurlResource.class);
        router.attach("/disks/{uuid}/turl", TurlResource.class);

        route = router.attach("/disks/{uuid}/?edit={" + DiskResource.EDIT_QUERY_VALUE + "}", DiskResource.class);
        route.setMatchingQuery(true);

        route = router.attach("/disks/{uuid}?edit={" + DiskResource.EDIT_QUERY_VALUE + "}", DiskResource.class);
        route.setMatchingQuery(true);

        router.attach("/disks/{uuid}/", DiskResource.class);
        router.attach("/disks/{uuid}", DiskResource.class);

        router.attach("/disks/", DisksResource.class);
        router.attach("/disks", DisksResource.class);

        router.attach("/instances/{vmid}/", InstancesResource.class);
        router.attach("/instances/{vmid}", InstancesResource.class);

        router.attach("/instances/", InstancesResource.class);
        router.attach("/instances", InstancesResource.class);

        router.attach("/", HomeResource.class);

        router.attach("/media/", createMediaDirectory(context));

        return createGuard(context, router);
    }

    private static Directory createMediaDirectory(Context context) {
        String mediaLocation = System.getProperty("media.content.location", "war:///media");
        Directory mediaDir = new Directory(context, mediaLocation);
        mediaDir.setNegotiatingContent(false);
        mediaDir.setIndexName("index.html");

        return mediaDir;
    }

    //
    // This guard is needed although JAAS is doing all of the
    // authentication. This allows the authentication information
    // to be retrieved from the request through the challenge
    // request.
    //
    private static ChallengeAuthenticator createGuard(Context context, Router next) {
        DummyVerifier verifier = new DummyVerifier();
        ChallengeAuthenticator guard = new ChallengeAuthenticator(context, ChallengeScheme.HTTP_BASIC,
                "Stratuslab Persistent Disk Storage");
        guard.setVerifier(verifier);
        guard.setNext(next);
        return guard;
    }

    private static Configuration createFreeMarkerConfig(Context context) {

        Configuration fmCfg = new Configuration();
        fmCfg.setLocalizedLookup(false);

        LocalReference fmBaseRef = LocalReference.createClapReference("/");
        fmCfg.setTemplateLoader(new ContextTemplateLoader(context, fmBaseRef));

        return fmCfg;
    }

    public Configuration getFreeMarkerConfiguration() {
        return freeMarkerConfiguration;
    }

    public static class RootRouter extends Router {

        ServiceConfiguration configuration = null;
        Configuration freeMarkerConfiguration = null;

        public RootRouter(Context context, ServiceConfiguration configuration, Configuration freeMarkerConfiguration) {
            super(context);
            this.configuration = configuration;
            this.freeMarkerConfiguration = freeMarkerConfiguration;
        }

        @Override
        public void doHandle(Restlet next, Request request, Response response) {

            Map<String, Object> attributes = request.getAttributes();

            attributes.put(SVC_CONFIGURATION_KEY, configuration);
            attributes.put(FM_CONFIGURATION_KEY, freeMarkerConfiguration);
            request.setAttributes(attributes);

            super.doHandle(next, request, response);
        }

    }

}
